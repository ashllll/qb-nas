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


async def test_ping_legacy_login_failure_does_not_blame_the_password_alone():
    """「登录失败」这句笼统话不能直接当成密码错。

    回归（独立复查实测）：transport 在 qB **连不上** 时抛的也是这句
    ``qBittorrent 登录失败``，以前按「含登录两个字」归成「用户名或密码不对」，
    用户照着改密码越改越乱。现在 transport 分得清三种原因；只剩这句无信息量
    的老消息时，两种可能都摆出来，别替用户下错结论。
    """
    client = make_client()

    async def auth(method, path, **kw):
        raise RuntimeError("qBittorrent 登录失败")

    client._req = auth  # type: ignore[method-assign]
    assert await client.ping() is False
    reason = client.last_error or ""
    assert "用户名或密码不对" in reason
    assert "离线" in reason or "不通" in reason


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
