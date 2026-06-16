"""Tests for QBitTransport cookie/session handling."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
import pytest_asyncio

from magnet_harvester.qbit_client._transport import QBitTransport
from magnet_harvester.qbit_client.stats import QBittorrentStats


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, cookies=None):
        self.text = text
        self.status_code = status_code
        self.cookies = cookies or httpx.Cookies()


@pytest_asyncio.fixture
async def transport():
    t = QBitTransport(
        host="http://qb:8080",
        username="user",
        password="pass",
        stats=QBittorrentStats(),
    )
    try:
        yield t
    finally:
        await t.close()


@pytest_asyncio.fixture
async def logged_in_transport(transport):
    """Transport with a pre-injected AsyncClient and mocked login cookies."""
    client = httpx.AsyncClient()
    transport._client = client

    async def fake_post(url, **kw):
        return FakeResponse(
            text="Ok.",
            status_code=200,
            cookies=httpx.Cookies({"SID": "abc123"}),
        )

    client.post = fake_post
    try:
        yield transport
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_authenticated_request_does_not_pass_cookies_kwarg(logged_in_transport):
    """After login, subsequent API calls must not use the deprecated
    per-request ``cookies=`` argument.
    """
    client = logged_in_transport._client
    request_calls = []

    async def fake_request(method, url, **kw):
        request_calls.append(kw)
        return FakeResponse(text="Ok.", status_code=200)

    client.request = fake_request

    r = await logged_in_transport.request("GET", "/app/version")

    assert r.status_code == 200
    assert logged_in_transport._cookie is not None
    assert all("cookies" not in call for call in request_calls), (
        "per-request cookies= was passed to httpx.AsyncClient.request"
    )


@pytest.mark.asyncio
async def test_relogin_updates_client_cookies_after_403(logged_in_transport):
    """A 403 response should trigger re-login and replace the client cookie jar
    with the new session cookies.
    """
    client = logged_in_transport._client
    login_calls = [0]
    request_calls = [0]

    async def fake_post(url, **kw):
        login_calls[0] += 1
        sid = "first" if login_calls[0] == 1 else "second"
        return FakeResponse(
            text="Ok.",
            status_code=200,
            cookies=httpx.Cookies({"SID": sid}),
        )

    async def fake_request(method, url, **kw):
        request_calls[0] += 1
        if request_calls[0] == 1:
            return FakeResponse(text="Forbidden", status_code=403)
        return FakeResponse(text="Ok.", status_code=200)

    client.post = fake_post
    client.request = fake_request

    r = await logged_in_transport.request("GET", "/app/version")

    assert r.status_code == 200
    assert login_calls[0] == 2
    assert client.cookies.get("SID") == "second"


@pytest.mark.asyncio
async def test_close_clears_session_cookies(logged_in_transport):
    """close() must drop both the internal cookie sentinel and the httpx
    client's cookie jar.
    """
    client = logged_in_transport._client

    async def fake_post(url, **kw):
        return FakeResponse(
            text="Ok.",
            status_code=200,
            cookies=httpx.Cookies({"SID": "abc123"}),
        )

    client.post = fake_post
    await logged_in_transport._login()
    assert logged_in_transport._cookie is not None
    assert client.cookies.get("SID") == "abc123"

    await logged_in_transport.close()

    assert logged_in_transport._cookie is None
    assert client.cookies.get("SID") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
