"""Focused tests for qBittorrent category creation and verification."""

from __future__ import annotations

import pytest

from magnet_harvester.config import QBitConfig
from magnet_harvester.qbit_client.client import QBittorrentClient


class FakeResponse:
    status_code = 200
    text = "Ok."


@pytest.mark.asyncio
async def test_ensure_category_waits_until_created_category_is_visible(monkeypatch):
    client = QBittorrentClient(config=QBitConfig(host="http://qb.example:8080"))
    category_snapshots = [
        {},
        {},
        {"电影": {"savePath": "/downloads/电影"}},
    ]
    requests = []
    sleeps = []

    async def fake_get_categories():
        if category_snapshots:
            return category_snapshots.pop(0)
        return {"电影": {"savePath": "/downloads/电影"}}

    async def fake_req(method, path, **kw):
        requests.append((method, path, kw))
        return FakeResponse()

    async def fake_sleep(delay):
        sleeps.append(delay)

    client.get_categories = fake_get_categories
    client._req = fake_req
    monkeypatch.setattr("magnet_harvester.qbit_client.client.asyncio.sleep", fake_sleep)

    assert await client.ensure_category("电影", "/downloads/电影") is True

    assert requests == [
        (
            "POST",
            "/torrents/createCategory",
            {"data": {"category": "电影", "savePath": "/downloads/电影"}},
        )
    ]
    assert sleeps


@pytest.mark.asyncio
async def test_ensure_category_fails_when_created_category_never_appears(monkeypatch):
    client = QBittorrentClient(config=QBitConfig(host="http://qb.example:8080"))
    requests = []

    async def fake_get_categories():
        return {}

    async def fake_req(method, path, **kw):
        requests.append((method, path, kw))
        return FakeResponse()

    async def fake_sleep(_delay):
        return None

    client.get_categories = fake_get_categories
    client._req = fake_req
    monkeypatch.setattr("magnet_harvester.qbit_client.client.asyncio.sleep", fake_sleep)

    assert await client.ensure_category("电影", "/downloads/电影") is False
    assert requests == [
        (
            "POST",
            "/torrents/createCategory",
            {"data": {"category": "电影", "savePath": "/downloads/电影"}},
        )
    ]
