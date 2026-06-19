"""Tests for QBitTransport cookie/session handling."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

from magnet_harvester.qbit_client._transport import QBitTransport
from magnet_harvester.qbit_client.stats import QBittorrentStats


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, cookies=None):
        self.text = text
        self.status_code = status_code
        self.cookies = cookies or httpx.Cookies()


class FakeAsyncClient:
    def __init__(self):
        self.cookies = httpx.Cookies()
        self.is_closed = False
        self.post_calls = []
        self.request_calls = []
        self._post = None
        self._request = None

    async def post(self, url, **kw):
        self.post_calls.append((url, kw))
        if self._post is None:
            return FakeResponse(text="Ok.", cookies=httpx.Cookies({"SID": "abc123"}))
        return await self._post(url, **kw)

    async def request(self, method, url, **kw):
        self.request_calls.append((method, url, kw))
        if self._request is None:
            return FakeResponse(text="Ok.", status_code=200)
        return await self._request(method, url, **kw)

    async def aclose(self):
        self.is_closed = True


def _make_transport(
    client: FakeAsyncClient,
    stats: QBittorrentStats | None = None,
) -> QBitTransport:
    return QBitTransport(
        host="http://qb:8080",
        username="user",
        password="pass",
        stats=stats or QBittorrentStats(),
        client_factory=lambda: client,
    )


@pytest.mark.asyncio
async def test_authenticated_request_does_not_pass_cookies_kwarg():
    """After login, subsequent API calls must not use the deprecated
    per-request ``cookies=`` argument.
    """
    client = FakeAsyncClient()
    transport = _make_transport(client)

    try:
        r = await transport.request("GET", "/app/version")
        assert r.status_code == 200
        assert client.cookies.get("SID") == "abc123"
        assert all("cookies" not in kw for _method, _url, kw in client.request_calls), (
            "per-request cookies= was passed to httpx.AsyncClient.request"
        )
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_relogin_updates_client_cookies_after_403():
    """A 403 response should trigger re-login and replace the client cookie jar
    with the new session cookies.
    """
    client = FakeAsyncClient()
    transport = _make_transport(client)
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

    client._post = fake_post
    client._request = fake_request

    try:
        r = await transport.request("GET", "/app/version")
        assert r.status_code == 200
        assert login_calls[0] == 2
        assert client.cookies.get("SID") == "second"
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_close_clears_session_cookies():
    """close() must drop both the internal cookie sentinel and the httpx
    client's cookie jar.
    """
    client = FakeAsyncClient()
    transport = _make_transport(client)

    await transport.request("GET", "/app/version")
    assert client.cookies.get("SID") == "abc123"

    await transport.close()

    assert client.cookies.get("SID") is None
    assert client.is_closed is True


@pytest.mark.asyncio
async def test_successful_request_resets_consecutive_failures():
    """A later successful qB request should restore the health counter."""
    client = FakeAsyncClient()
    stats = QBittorrentStats(consecutive_failures=3)
    transport = _make_transport(client, stats=stats)

    try:
        r = await transport.request("GET", "/app/version")
        assert r.status_code == 200
        assert stats.consecutive_failures == 0
        assert stats.last_success_time is not None
    finally:
        await transport.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
