"""统一的错误响应体（约定第 2 条）：非 2xx 一律 ``{detail, code, hint}``。

为什么放在应用层：FastAPI 默认只回 ``{"detail": "..."}``，401 更是把
"缺少 key" 和 "key 不对" 混成同一句 ``Invalid or missing API key``，
页面于是只能把真正的权限原因埋进一句无信息量的话里。

这里做四件事：
1. HTTPException → 契约体；401 按请求头有没有 X-API-Key 区分缺失/不对。
2. RequestValidationError → 422，detail 指出是哪个字段、错在哪。
3. 未捕获异常 → 500，detail 带异常类型与脱敏后的消息，并写完整堆栈到日志。
4. 任何 detail/hint 都过一遍脱敏，API Key 不会因错误消息回到页面或日志。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

# 状态码兜底：服务端没给 code/hint 时也要能说"下一步做什么"
_STATUS_META: dict[int, tuple[str, str]] = {
    400: (
        "bad_request",
        "请求没有被接受。刷新页面后重试；如果一直出现，把这条提示和发生时间交给维护者。",
    ),
    401: (
        "api_key_missing",
        "在页面右侧「连接设置」填入与 NAS 上 .env 里 API_KEY 相同的值，再保存。",
    ),
    403: (
        "forbidden",
        "这次操作被服务端拒绝；确认 API Key 对应的是这台 NAS。",
    ),
    404: (
        "not_found",
        "这项资源或任务已经不存在了；刷新列表看看最新状态。",
    ),
    409: (
        "conflict",
        "这条记录刚被别处改过；刷新后重试。",
    ),
    422: (
        "validation_error",
        "表单里有字段不符合要求，按提示改好再提交。",
    ),
    429: (
        "rate_limited",
        "请求太频繁了；等几秒再试。",
    ),
    500: (
        "internal_error",
        "NAS 上的服务出错了。重试一次；仍失败就把这条提示和发生时间发给维护者，并附上 NAS 日志里的同刻记录。",
    ),
    502: (
        "upstream_unavailable",
        "服务暂时不可用（可能 qBittorrent 或采集服务离线）。稍等再试；持续出现请查 NAS 日志。",
    ),
    503: (
        "service_unavailable",
        "服务暂时不可用（可能 qBittorrent 或采集服务离线）。稍等再试；持续出现请查 NAS 日志。",
    ),
    504: (
        "upstream_timeout",
        "上游超时。检查 qBittorrent 是否在线，稍后重试。",
    ),
}

# 用户/日志里都不许出现的凭据形态
_SECRET_PATTERNS = (
    re.compile(r"(api_key=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(x-api-key\s*[:=]\s*)[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"(authorization\s*[:=]\s*)[^\s,;\"']+", re.IGNORECASE),
)

_LEGACY_401_DETAILS = {
    "invalid or missing api key",
    "unauthorized",
    "not authenticated",
}


def redact(text: str) -> str:
    """去掉错误消息里可能出现的凭据，其余原样保留（原因不能被一起删掉）。"""
    result = str(text or "")
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(r"\1***", result)
    return result


def _meta(status_code: int) -> tuple[str, str]:
    return _STATUS_META.get(
        status_code,
        (
            f"http_{status_code}",
            "重试一次；仍失败就把这条提示和发生时间发给维护者。",
        ),
    )


def _payload(
    status_code: int,
    detail: str,
    *,
    code: str | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    fallback_code, fallback_hint = _meta(status_code)
    return {
        "detail": redact(detail or ""),
        "code": code or fallback_code,
        "hint": redact(hint) if hint else fallback_hint,
    }


def _http_exception_payload(request: Request, exc: StarletteHTTPException) -> dict[str, Any]:
    status_code = exc.status_code
    raw = exc.detail
    detail = ""
    code: str | None = None
    hint: str | None = None
    if isinstance(raw, dict):
        detail = str(raw.get("detail") or raw.get("message") or "")
        code = str(raw.get("code") or "") or None
        hint = raw.get("hint") or None
    else:
        detail = str(raw or "")

    if status_code == 401:
        has_key = bool((request.headers.get("x-api-key") or "").strip())
        code = code or ("api_key_invalid" if has_key else "api_key_missing")
        if not detail or detail.strip().lower() in _LEGACY_401_DETAILS:
            detail = "API Key 不正确" if has_key else "请求缺少 API Key"
        hint = hint or (
            "当前保存的 API Key 与服务端不一致。到页面右侧「连接设置」核对 NAS 上 .env 里的 "
            "API_KEY 后重新保存。"
            if has_key
            else "在页面右侧「连接设置」填入 NAS 上 .env 里 API_KEY 的值，再保存。"
        )
    return _payload(status_code, detail, code=code, hint=hint)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    payload = _http_exception_payload(request, exc)
    if exc.status_code >= 500:
        log.error(
            "HTTP %s %s → %s", exc.status_code, request.url.path, payload["detail"]
        )
    return JSONResponse(
        payload,
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors() or []
    parts: list[str] = []
    for error in errors[:3]:
        loc = [str(part) for part in error.get("loc", []) if part != "body"]
        field = ".".join(loc)
        message = str(error.get("msg") or "").removeprefix("Value error, ").strip()
        parts.append(f"{field}：{message}" if field else message)
    detail = "参数校验失败：" + "；".join(parts) if parts else "参数校验失败"
    return JSONResponse(
        _payload(422, detail, code="validation_error", hint="按提示改好字段后重新提交。"),
        status_code=422,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("未捕获异常：%s %s", request.method, request.url.path)
    summary = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
    detail = f"服务内部错误（{summary[:300]}）" if summary else "服务内部错误"
    return JSONResponse(_payload(500, detail), status_code=500)


def install_error_handlers(app: FastAPI) -> None:
    """注册全局错误体处理器；重复调用安全。"""
    if getattr(app.state, "error_contract_installed", False):
        return
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.state.error_contract_installed = True
