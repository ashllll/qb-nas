"""授权模型（N23）：局域网信任 + 程序 API Key + 跨源/Host 防护。

用户定下的口径（2026-11-01 N23）：

* 浏览器零密钥：页面不保存、不读取、不发送任何 key；
* 局域网内打开即可读写；危险动作由 UI 二次确认；
* API Key 只留给程序（Mac 代理）走 ``X-API-Key``；
* 若日后把服务暴露到局域网之外，用显式开关切回「必须 key」。

这个模块是**唯一**的授权判定点。页面（``/api/auth``）、写接口
（``Depends(require_api_key)``）和实时通道（WS）都从这里取同一份结论，
不各自猜。

判定顺序（为什么这样排）：

1. **Host 是否可接受**。正式档容器是 bridge 端口映射，容器内看到的
   ``request.client.host`` 会被 NAT 成 docker 网关，不能拿它判「局域网来源」。
   Host 头是端到端保留的（bridge 不改写），所以用它当信任锚：IP 字面量
   （loopback/私网/链路本地）、``localhost``、单标签局域网名（``nas``）、
   ``*.local`` 视为本地；带点的公网域名必须显式列进 ``TRUSTED_HOSTS``。
   DNS rebinding 用的攻击者域名一定带点，于是被挡在门外——读和写都挡，
   因为 rebinding 本来就是为了读数据。
2. **浏览器发起的写必须同源**。有 ``Origin`` 时它必须与请求的 Host 同源；
   ``Sec-Fetch-Site`` 必须是 ``same-origin`` / ``none``。没有 ``Origin`` 的
   程序请求（curl、Mac 代理）照常放行。
3. **模式**。``trusted``（默认）：放行，带正确 key 仍放行，错 key 不阻断；
   ``strict``：写必须带正确 ``X-API-Key``。两种模式都先过 1、2。
"""

from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request, status

from magnet_harvester.config import settings

TRUSTED = "trusted"
STRICT = "strict"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def auth_mode_of(value: object) -> str:
    """``AUTH_MODE`` 的唯一解释：只认 strict，其余一律 trusted（默认）。"""
    return STRICT if str(value or "").strip().lower() == STRICT else TRUSTED


def current_auth_mode() -> str:
    return auth_mode_of(getattr(settings, "AUTH_MODE", TRUSTED))


@dataclass(frozen=True)
class AuthContext:
    """一次请求的授权结论；``/api/auth`` 与写接口共用这一份。"""

    mode: str
    trusted: bool  # 这份请求现在不用 key 就能写
    write: bool  # 这份请求现在能写（trusted，或 strict 下 key 正确/未配置）
    required: bool  # 这个部署是否要求 key（strict 且配置了 key）
    key_state: str  # not_required / missing / valid / invalid
    reason: str  # trusted_lan / strict_mode / no_key_configured / untrusted_host / cross_origin
    host_ok: bool
    origin_ok: bool

    def as_auth_payload(self) -> dict[str, object]:
        """``GET /api/auth`` 的公开字段：只回答状态，绝不回声任何凭据。"""
        return {
            "mode": self.mode,
            "trusted": self.trusted,
            "write": self.write,
            "required": self.required,
            "key": self.key_state,
            "reason": self.reason,
            # 旧的字段名保留：上一轮的页面/脚本按它判断「要不要钥匙」。
            "scheme": "X-API-Key" if self.required else None,
            "websocket": "first-message" if self.required else "open",
        }


# ── Host ────────────────────────────────────────────────────────────────


def hostname_of(host_header: str | None) -> str:
    """从 Host 头取出小写主机名（去端口、去 IPv6 方括号）。"""
    raw = (host_header or "").strip()
    if not raw:
        return ""
    if raw.startswith("["):  # [::1]:8899
        end = raw.find("]")
        return raw[1:end].strip().lower() if end > 0 else ""
    if raw.count(":") == 1:
        host, _, port = raw.partition(":")
        if port.isdigit():
            return host.strip().lower()
    return raw.lower()


def _configured_trusted_hosts() -> set[str]:
    raw = str(getattr(settings, "TRUSTED_HOSTS", "") or "")
    return {hostname_of(item.strip()) for item in raw.split(",") if item.strip()}


def _is_local_hostname(host: str) -> bool:
    if not host:
        return False
    if host in {"localhost", "0.0.0.0", "::", "::1"}:
        return True
    if host.endswith(".localhost") or host.endswith(".local"):
        # .localhost 是保留名；.local 是 mDNS 保留后缀，都不会是攻击者注册的域名。
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 单标签主机名（nas / qb-nas / testserver）只能靠局域网 DNS/mDNS 解析，
        # 不是从公网可注册的名字；DNS rebinding 的域名一定带点。
        return "." not in host
    return address.is_loopback or address.is_private or address.is_link_local


def host_allowed(host_header: str | None) -> bool:
    """Host 头是否属于「本机/局域网」或显式允许的域名。"""
    host = hostname_of(host_header)
    if not host:
        return False
    if host in _configured_trusted_hosts():
        return True
    return _is_local_hostname(host)


# ── Origin / Sec-Fetch-Site ─────────────────────────────────────────────


def _authority(value: str, *, default_port: int) -> tuple[str, int] | None:
    """把 ``host[:port]`` / ``scheme://host[:port]`` 归一成 (hostname, port)。"""
    text = (value or "").strip()
    if not text or text.lower() == "null":
        return None
    if "://" in text:
        parts = urlsplit(text)
        scheme = (parts.scheme or "http").lower()
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme, default_port)
    else:
        host = hostname_of(text)
        port_text = text.rsplit(":", 1)[-1] if text.count(":") == 1 else ""
        port = int(port_text) if port_text.isdigit() else default_port
    if not host:
        return None
    return host, int(port)


def _configured_origins() -> set[str]:
    raw = str(getattr(settings, "CORS_ALLOWED_ORIGINS", "") or "")
    return {item.strip().rstrip("/").lower() for item in raw.split(",") if item.strip()}


def origin_allowed(headers, scheme: str = "http") -> bool:
    """浏览器发起的跨源写一律拒绝；没有 Origin 的程序请求放行。

    ``headers`` 是 Host/Origin/Sec-Fetch-Site 的映射（Request.headers 或
    ASGI 中间件里的 Headers）；``scheme`` 用来补默认端口。

    ``Sec-Fetch-Site`` 是浏览器自己打的标签，攻击页面无法伪造成
    ``same-origin``；两个信号都看，是为了覆盖只带其中一个的浏览器。
    """
    origin = (headers.get("origin") or "").strip()
    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()

    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return False

    if not origin:
        return True

    normalized = origin.rstrip("/").lower()
    if normalized in _configured_origins():
        return True

    request_scheme = (scheme or "http").lower()
    request_host = (headers.get("host") or "").strip().lower()
    origin_authority = _authority(origin, default_port=_DEFAULT_PORTS.get(request_scheme, 80))
    request_authority = _authority(
        request_host, default_port=_DEFAULT_PORTS.get(request_scheme, 80)
    )
    return bool(origin_authority and origin_authority == request_authority)


# ── 判定 ────────────────────────────────────────────────────────────────


def _expected_key(request: Request) -> str:
    ctx = getattr(request.app.state, "ctx", None)
    runtime = getattr(ctx, "runtime", None)
    value = getattr(runtime, "api_key", None)
    if value is None:
        value = getattr(settings, "API_KEY", "")
    return str(value or "").strip()


def _safe_compare(supplied: str, expected: str) -> bool:
    """常量时间比较；非 ASCII 字符串按认证失败处理（对齐 WS 路径）。"""
    try:
        return secrets.compare_digest(supplied, expected)
    except TypeError:
        return False


def resolve_auth(request: Request) -> AuthContext:
    """计算一份请求的授权结论；``/api/auth``、写依赖和 WS 都调它。"""
    mode = current_auth_mode()
    host_ok = host_allowed(request.headers.get("host"))
    origin_ok = origin_allowed(request.headers, request.url.scheme)

    expected = _expected_key(request)
    supplied = (request.headers.get("x-api-key") or "").strip()

    if not expected:
        key_state = "not_required"
    elif not supplied:
        key_state = "missing"
    elif _safe_compare(supplied, expected):
        key_state = "valid"
    else:
        key_state = "invalid"

    trusted = mode == TRUSTED and host_ok and origin_ok
    required = mode == STRICT and bool(expected)
    write = host_ok and origin_ok and (trusted or not expected or key_state == "valid")

    if not host_ok:
        reason = "untrusted_host"
    elif not origin_ok:
        reason = "cross_origin"
    elif mode == TRUSTED:
        reason = "trusted_lan"
    elif not expected:
        reason = "no_key_configured"
    else:
        reason = "strict_mode"

    return AuthContext(
        mode=mode,
        trusted=trusted,
        write=write,
        required=required,
        # trusted 模式下 key 永远不是门：即使这次请求因 Host/Origin 不可信而不能写，
        # 状态也如实说「不需要 key」，原因由 reason/write 表达，避免页面误以为缺钥匙。
        key_state="not_required" if mode == TRUSTED or not expected else key_state,
        reason=reason,
        host_ok=host_ok,
        origin_ok=origin_ok,
    )


def host_block_payload() -> dict[str, str]:
    return {
        "detail": "这个地址不在 NAS 允许访问的主机名列表里",
        "code": "untrusted_host",
        "hint": "用 NAS 的局域网 IP（如 http://192.168.20.193:8899）打开；"
        "如果是通过域名/反向代理访问，把这个域名加进 NAS config/.env 的 TRUSTED_HOSTS。",
    }


def cross_origin_payload() -> dict[str, str]:
    return {
        "detail": "跨源请求被拒绝",
        "code": "cross_origin_blocked",
        "hint": "写操作只能从 NAS 页面本身发起。程序调用请直接请求 API（不带浏览器 Origin）。",
    }


async def require_api_key(
    request: Request, x_api_key: str | None = Header(None)
) -> None:
    """写接口的授权依赖：Host/Origin 先过，再按模式决定要不要 key。

    信任模式（默认）：不带 key 放行；带正确 key 放行；带错 key 也不阻断——
    key 在这个部署里不是门，留着它只是为了 Mac 代理这类程序通道兼容。
    非信任模式：写必须带正确 key；页面没有 key，于是只读。
    """
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service initializing",
        )

    auth = resolve_auth(request)
    if not auth.host_ok:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=host_block_payload())
    if request.method.upper() not in SAFE_METHODS and not auth.origin_ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=cross_origin_payload()
        )
    if auth.write:
        return

    if auth.key_state == "missing":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "detail": "缺少 X-API-Key 请求头",
                "code": "api_key_missing",
                "hint": "这台设备当前只读。浏览器页面在局域网内不需要 key；"
                "程序调用请在 X-API-Key 头里带上 NAS config/.env 里的 API_KEY。",
            },
            headers={"WWW-Authenticate": "X-API-Key"},
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "detail": "X-API-Key 不正确",
            "code": "api_key_invalid",
            "hint": "确认 key 与 NAS config/.env 里的 API_KEY 一致（重装/换机后最容易对不上）。",
        },
        headers={"WWW-Authenticate": "X-API-Key"},
    )
