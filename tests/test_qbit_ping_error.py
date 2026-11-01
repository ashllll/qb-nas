"""qB 离线时要说清原因：ping() 失败必须把可读原因留给 last_error。

回归：界面只能显示「qB 离线」+ 静态提示，用户分不清是网络不通、服务没起、
还是用户名/密码不对（⑤ 类：失败要指向原因）。
"""

from __future__ import annotations

import httpx

from magnet_harvester.config import QBitConfig
from magnet_harvester.qbit_client.client import QBittorrentClient

URL = "http://qbit.test/app/version"


def make_client() -> QBittorrentClient:
    return QBittorrentClient(QBitConfig(host="http://qbit.test"))


async def test_ping_transport_error_records_reason():
    client = make_client()

    async def boom(method, path, **kw):
        raise httpx.ConnectError("connection refused")

    client._req = boom  # type: ignore[method-assign]
    assert await client.ping() is False
    assert client.last_error
    assert "连接 qBittorrent 失败" in client.last_error
    assert "ConnectError" in client.last_error


async def test_ping_timeout_records_reason():
    client = make_client()

    async def slow(method, path, **kw):
        raise httpx.ReadTimeout("timed out")

    client._req = slow  # type: ignore[method-assign]
    assert await client.ping() is False
    assert "超时" in (client.last_error or "")


async def test_ping_auth_failure_records_reason():
    client = make_client()

    async def auth(method, path, **kw):
        raise RuntimeError("qBittorrent 登录失败")

    client._req = auth  # type: ignore[method-assign]
    assert await client.ping() is False
    assert "认证失败" in (client.last_error or "")


async def test_ping_http_status_records_reason():
    client = make_client()

    async def server_error(method, path, **kw):
        return httpx.Response(503, request=httpx.Request("GET", URL))

    client._req = server_error  # type: ignore[method-assign]
    assert await client.ping() is False
    assert "HTTP 503" in (client.last_error or "")


async def test_ping_success_clears_stale_reason():
    client = make_client()
    client.stats.error("连接 qBittorrent 失败：旧的")

    async def ok(method, path, **kw):
        return httpx.Response(200, request=httpx.Request("GET", URL))

    client._req = ok  # type: ignore[method-assign]
    assert await client.ping() is True
    assert client.last_error is None
