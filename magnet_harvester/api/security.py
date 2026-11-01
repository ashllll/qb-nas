"""请求入口的安全护栏：Host（DNS rebinding）与跨源写（CSRF）。

为什么是中间件而不是每个接口的依赖：

* DNS rebinding 的攻击面是**读**——攻击者页面把自己的域名解析到 NAS 的
  局域网 IP，浏览器于是把 ``http://evil.example`` 当成同源，``fetch``
  回来的 JSON 就能被读走。只给写接口加依赖挡不住这条路，所以 Host 校验
  必须覆盖 API、页面和静态资源；WS 握手也走同一层。
* 跨源写（CSRF）同理由入口统一挡，接口层的 ``require_api_key`` 再做一次
  （防御纵深），两处共用 ``utils/auth.py`` 的同一份判定。

判定细节见 ``magnet_harvester.utils.auth``；这里只负责在请求进门时执行。
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from magnet_harvester.utils.auth import (
    SAFE_METHODS,
    cross_origin_payload,
    host_allowed,
    host_block_payload,
    origin_allowed,
)


class SecurityGuardMiddleware:
    """在进入应用前拒绝不可信的 Host 与跨源状态变更请求。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if not host_allowed(headers.get("host")):
            if scope["type"] == "websocket":
                # 握手阶段直接关掉：DNS rebinding 不能借 WS 读实时数据。
                await send({"type": "websocket.close", "code": 4403})
                return
            response = JSONResponse(host_block_payload(), status_code=403)
            await response(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        if (scope["type"] == "websocket" or method not in SAFE_METHODS) and not origin_allowed(
            headers, str(scope.get("scheme") or "http")
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
                return
            response = JSONResponse(cross_origin_payload(), status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def install_security_guard(app) -> None:
    """装上入口护栏；重复调用安全。"""
    if getattr(app.state, "security_guard_installed", False):
        return
    app.add_middleware(SecurityGuardMiddleware)
    app.state.security_guard_installed = True
