"""Magnet Harvester v3.0 — app entrypoint and lifespan assembly."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from magnet_harvester.assembly import build_runtime
from magnet_harvester.api.pages import STATIC_DIR, router as pages_router
from magnet_harvester.api.routes import router as api_router
from magnet_harvester.api.websocket import router as ws_router
from magnet_harvester.config import settings
from magnet_harvester.logger import configure_logging
from magnet_harvester.utils.interface_guard import (
    needs_interface_guard,
    request_arrived_on_non_loopback_interface,
)

configure_logging(
    level=settings.LOG_LEVEL,
    log_file=settings.LOG_FILE or None,
)
log = logging.getLogger(__name__)


def _configure_cors(app: FastAPI) -> None:
    """在应用启动前配置 CORS 中间件。"""
    cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )


def _host_from_argv() -> str | None:
    """从 uvicorn CLI 参数中取 --host（覆盖 SERVICE_HOST 的情形）。

    实测 CLI 启动时应用可见：['...uvicorn/__main__.py', 'app:app', '--host',
    '0.0.0.0', '--port', '8899']。`uvicorn.run()` 脚本路径下 argv 不含参数
    （那条路由启动脚本自己设置 MH_BOUND_HOST）。
    """
    argv = sys.argv or []
    for index, token in enumerate(argv):
        if token.startswith("--host="):
            return token.split("=", 1)[1].strip() or None
        if token == "--host" and index + 1 < len(argv):
            return argv[index + 1].strip() or None
    return None


def _bound_host() -> str | None:
    """取本次运行**实际绑定**的监听地址，取不到返回 None。

    只校验 settings.SERVICE_HOST 会被 --host 覆盖绕过：环境变量仍是 127.0.0.1，
    服务却真的绑到了 LAN。uvicorn 不把 host 写进环境变量，Server.current 在
    lifespan 时也尚未赋值（实测为 None），因此按优先级依次尝试：
    显式契约（run.py 设置 MH_BOUND_HOST）→ uvicorn CLI 参数 → UVICORN_HOST。
    """
    for name in ("MH_BOUND_HOST", "UVICORN_HOST"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return _host_from_argv()


def _api_key_required_for_writes() -> bool:
    """写操作是否已具备鉴权（配置了 API_KEY 或显式开发豁免）。"""
    return bool((settings.API_KEY or "").strip()) or bool(settings.ALLOW_INSECURE_WRITE_API)


async def _interface_guard(request, call_next):
    """兜底：经非 loopback 网卡进入的无鉴权写请求一律拒绝。

    与启动期校验互补 —— 启动期只覆盖"应用能得知真实绑定地址"的情况，而本层依据
    ASGI scope 的 server 字段（该连接被接受的本机接口地址）逐请求判断，与启动
    方式无关。本机 loopback 访问不受影响。
    """
    if not _api_key_required_for_writes() and needs_interface_guard(request.scope):
        if request_arrived_on_non_loopback_interface(request.scope):
            log.warning(
                "拒绝经非 loopback 接口进入的无鉴权 %s %s",
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "Refusing unauthenticated write access from a non-loopback "
                        "interface. Configure API_KEY or set ALLOW_INSECURE_WRITE_API=true."
                    )
                },
            )
    return await call_next(request)


# ═══════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_security_posture(bound_host=_bound_host())

    runtime = build_runtime()
    app.state.ctx = runtime.ctx
    try:
        await runtime.start()
    except Exception:
        log.exception("runtime.start() 失败")
        # 验证核心存储服务是否可用：store 不可用为致命错误，应阻止启动
        try:
            await runtime.ctx.core.store.count()
        except Exception:
            log.critical("核心存储 store 不可用，无法启动")
            raise
        log.warning("runtime.start() 部分失败，继续以降级模式运行")

    # qBittorrent 连接检查（可降级：离线时服务仍可运行）
    qbit_ok = False
    try:
        qbit_ok = await runtime.ctx.core.qbit.ping()
    except Exception:
        log.warning("qBittorrent 连接检查失败，继续以降级模式运行")

    disk_info = settings.check_disk_space()
    log.info(
        f"Scrapling Spider 已就绪 | qB: {'在线' if qbit_ok else '离线'} "
        f"| 本地分类器就绪 | 磁盘: {disk_info.get('free_gb', '?')}GB"
    )

    yield

    await runtime.stop()
    log.info("服务已关闭")


app = FastAPI(title="Magnet Harvester v3.0", lifespan=lifespan)

_configure_cors(app)
# 兜底守卫：经非 loopback 接口进入的无鉴权写请求一律拒绝（与启动期校验互补）
app.middleware("http")(_interface_guard)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(pages_router)
app.include_router(api_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn

    from magnet_harvester.logger import uvicorn_log_config

    uvicorn.run(
        "magnet_harvester.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=False,
        log_config=uvicorn_log_config(),
    )
