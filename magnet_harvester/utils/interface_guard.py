"""接口来源守卫：判断访问是否经非 loopback 网卡进入本机。

背景：API_KEY 为空时写接口不做鉴权（本地回环默认部署的兼容模式）。但若服务被
绑定到非 loopback 地址（含 `uvicorn ... --host 0.0.0.0` 这类覆盖 SERVICE_HOST 的
启动方式），LAN 上就会出现无鉴权写接口。启动期校验只能覆盖"应用能得知真实绑定
地址"的情况，因此这里再提供一层与启动方式无关的兜底：

ASGI scope 的 "server" 是**该连接被接受的本机接口地址**。实测在绑定 0.0.0.0 时：
经 loopback 访问为 ('127.0.0.1', port)，经 LAN 访问为 ('192.168.0.230', port)。
因此可逐请求判断"这次访问是从哪个接口进来的"，无需知道启动参数。
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

# 需要写鉴权的 HTTP 方法（与 require_api_key 的适用范围一致）
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_non_loopback_interface(address: str | None) -> bool:
    """本机接口地址是否为非 loopback（即该连接经对外网卡进入）。"""
    if not address:
        return False
    host = address.strip().strip("[]").lower()
    if not host or host in {"0.0.0.0", "::"}:
        # 通配地址本身无法判断接口，交由具体连接的 server 字段判断
        return False
    if host == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def request_arrived_on_non_loopback_interface(scope: Mapping[str, Any]) -> bool:
    """该请求是否来自非 loopback 来源。

    两侧都要看，缺一不可：

    - ``server``：该连接被接受的**本机接口地址**。绑定 0.0.0.0 时经 loopback 为
      127.0.0.1、经 LAN 为真实网卡地址，因此能识别"直接经对外网卡进来"。
    - ``client``：连接来源地址。**本机反向代理**（nginx/traefik 反代到 127.0.0.1
      后端）会让 server 恒为 127.0.0.1，此时只有 client 能反映真实来源；uvicorn
      的 proxy-headers 会按 X-Forwarded-For 重写该字段（实测）。

    两者都是 loopback 才算本机访问；任一为非 loopback 即视为外部来源。本机浏览器
    用 LAN IP 访问自己也会命中（client 为该 IP），属预期：改用 localhost 或配置
    API_KEY。
    """
    server = scope.get("server")
    client = scope.get("client")

    server_addr = server[0] if isinstance(server, (tuple, list)) and server else None
    client_addr = client[0] if isinstance(client, (tuple, list)) and client else None

    return is_non_loopback_interface(
        server_addr if isinstance(server_addr, str) else None
    ) or is_non_loopback_interface(client_addr if isinstance(client_addr, str) else None)


def needs_interface_guard(scope: Mapping[str, Any]) -> bool:
    """该请求是否属于需要守卫的范畴：写方法或 WebSocket 升级。"""
    if scope.get("type") == "websocket":
        return True
    return str(scope.get("method", "")).upper() in WRITE_METHODS
