"""qB 不在线时，界面拿到的原因必须是真话（而且不能是空）。

实测（合并候选 1014a42）：

- ``/api/v1/health`` 走的是 1.5 秒超时的探针；qB 主机不可达时登录重试要 2~3 秒，
  探测被超时取消，而「取消」不走 ``ping`` 的失败分支 → ``last_error`` 还是空的，
  接口回 ``reason: null``。用户在界面上只能看到「qB 离线」，不知道是地址、密码还是网络。
- 直接 ``ping``（下载、保存并测试都走这条）时，transport 对「连不上」和「密码不对」
  抛的是同一句 ``qBittorrent 登录失败``，``ping`` 只好按「含『登录』两个字」猜成
  **「认证失败：用户名或密码不对」**——qB 只是关机了，用户改密码只会更糟。
"""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.config import QBitConfig
from magnet_harvester.qbit_client.client import QBittorrentClient
from magnet_harvester.services.probe import CachedProbe


@pytest.fixture
def short_probe():
    """把模块级探针换成「短超时且干净」的，用完放回去（别影响别的用例）。"""
    from magnet_harvester.api import ingest_routes

    original = ingest_routes._qbit_probe
    ingest_routes._qbit_probe = CachedProbe(ttl=10.0, timeout=0.2)
    try:
        yield ingest_routes._qbit_probe
    finally:
        ingest_routes._qbit_probe = original


def make_client(host: str = "http://127.0.0.1:9") -> QBittorrentClient:
    return QBittorrentClient(
        QBitConfig(host=host, username="admin", password="secret", fs_base_path="")
    )


@pytest.mark.asyncio
async def test_unreachable_qb_does_not_blame_the_password():
    """qB 连不上 ≠ 密码不对。"""
    client = make_client("http://127.0.0.1:9")
    try:
        assert await client.ping() is False
        reason = client.last_error or ""
        assert reason, "qB 不可达时必须留下原因"
        assert "连接" in reason or "连不上" in reason
        assert "用户名或密码" not in reason, f"把网络故障说成密码错：{reason}"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_probe_timeout_still_leaves_a_reason():
    """/api/v1/health 的 1.5 秒探针超时后，last_error 不能是空的。"""
    client = make_client("http://127.0.0.1:9")

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(30)

    client._req = _hang  # 模拟「连得上但一直不回」的 qB
    try:
        probe = CachedProbe(ttl=10.0, timeout=0.2)
        assert await probe.read(client.ping) is False
        reason = client.last_error or ""
        assert reason, "探测超时也要留下原因，否则界面只能写「qB 离线」"
        assert "没有响应" in reason or "超时" in reason
    finally:
        await client.close()


def test_health_endpoint_reports_a_reason_when_qb_is_silent(tmp_path, short_probe):
    """端到端：/api/v1/health 的 qbit.reason 不能是 null。"""
    from tests._client import asgi_client
    from tests.test_ingest_api import make_app

    client = make_client("http://127.0.0.1:9")

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(30)

    client._req = _hang
    app, ctx = make_app(tmp_path)
    ctx.core.qbit = client
    try:
        with asgi_client(app) as api:
            response = api.get("/api/v1/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["qbit"]["ok"] is False
        assert payload["qbit"]["reason"], "qB 不回话时界面要能说出原因"
    finally:
        asyncio.run(client.close())


@pytest.mark.asyncio
async def test_rejected_credentials_do_say_password():
    """对照面：qB 真的拒了凭据时，原因里必须是「用户名或密码不对」。"""
    import httpx

    from magnet_harvester.qbit_client._transport import QBitTransport

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Fails.")
        return httpx.Response(200, text="v5.2.4")

    fake = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = make_client("http://qbit.test")
    client._transport = QBitTransport(
        host="http://qbit.test",
        username="admin",
        password="wrong",
        stats=client.stats,
        client_factory=lambda: fake,
    )
    try:
        assert await client.ping() is False
        reason = client.last_error or ""
        assert "用户名或密码不对" in reason, reason
        assert "连接不上" not in reason, reason
    finally:
        await client.close()
        await fake.aclose()


@pytest.mark.asyncio
async def test_banned_ip_is_named_as_banned():
    """qB 封 IP（Too many failed login attempts）时，别让用户去改密码。"""
    import httpx

    from magnet_harvester.qbit_client._transport import QBitTransport

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Your IP has been banned, wait 60 seconds.")
        return httpx.Response(200, text="v5.2.4")

    fake = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = make_client("http://qbit.test")
    client._transport = QBitTransport(
        host="http://qbit.test",
        username="admin",
        password="secret",
        stats=client.stats,
        client_factory=lambda: fake,
    )
    try:
        assert await client.ping() is False
        reason = client.last_error or ""
        assert "封禁" in reason, reason
    finally:
        await client.close()
        await fake.aclose()


def test_health_reason_falls_back_to_the_probe(tmp_path, short_probe):
    """客户端自己没留原因时（例如被别的 ping 挡在锁上），探针也要给出原因。"""
    from tests._client import asgi_client
    from tests.test_ingest_api import make_app

    class SilentQbit:
        last_error = None

        async def ping(self):
            await asyncio.sleep(30)
            return False

    app, ctx = make_app(tmp_path)
    ctx.core.qbit = SilentQbit()

    with asgi_client(app) as api:
        payload = api.get("/api/v1/health").json()

    assert payload["qbit"]["ok"] is False
    assert payload["qbit"]["reason"], "没有客户端原因时不能让 reason 变回 null"
    assert "没拿到回答" in payload["qbit"]["reason"] or "探测" in payload["qbit"]["reason"]
