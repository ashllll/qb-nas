"""Tests for API key authentication middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from magnet_harvester.bus import EventType
from tests._client import asgi_client
from magnet_harvester.config import settings
from magnet_harvester.main import app

import magnet_harvester.assembly as assembly_module


class _FakeCrawler:
    """Minimal crawler double — avoids real Scrapling/SQLite init."""

    def __init__(self, config, site_auth=None):
        self.max_depth = 3

    async def start(self):
        pass

    async def stop(self):
        pass


class _FakeQbit:
    def __init__(self, config):
        pass

    async def ping(self):
        return True

    async def close(self):
        pass

    def get_stats(self):
        return {}


class _FakeSyncLoop:
    def __init__(
        self, qbit_client, store, bus, task_manager=None, downloads=None, poll_interval=2.0
    ):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


class _FakeBroadcaster:
    def __init__(self, bus, store=None):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_INSECURE_WRITE_API", True)
    monkeypatch.setattr(assembly_module, "MagnetCrawler", _FakeCrawler)
    monkeypatch.setattr(assembly_module, "QBittorrentClient", _FakeQbit)
    monkeypatch.setattr(assembly_module, "QBitSyncLoop", _FakeSyncLoop)
    monkeypatch.setattr(assembly_module, "WSBroadcaster", _FakeBroadcaster)
    with asgi_client(app) as c:
        ctx = c.app.state.ctx
        ctx.core.pipeline.admit_crawl_target = AsyncMock(return_value="https://example.com")
        ctx.runtime.api_key = "test-secret-key-123"
        yield c


class TestAPIKeyAuth:
    """Verify sensitive endpoints require API key."""

    def test_crawl_without_key_returns_401(self, client):
        r = client.post("/api/crawl", json={"url": "https://example.com", "depth": 1})
        assert r.status_code == 401

    def test_crawl_with_valid_key_succeeds(self, client):
        r = client.post(
            "/api/crawl",
            json={"url": "https://example.com", "depth": 1},
            headers={"X-API-Key": "test-secret-key-123"},
        )
        assert r.status_code == 200

    def test_crawl_with_invalid_key_returns_401(self, client):
        r = client.post(
            "/api/crawl",
            json={"url": "https://example.com", "depth": 1},
            headers={"X-API-Key": "wrong-key"},
        )
        assert r.status_code == 401

    def test_download_without_key_returns_401(self, client):
        r = client.post("/api/download", json={"hashes": ["abc123"]})
        assert r.status_code == 401

    def test_download_with_key_succeeds(self, client):
        r = client.post(
            "/api/download",
            json={"hashes": ["abc123"]},
            headers={"X-API-Key": "test-secret-key-123"},
        )
        assert r.status_code == 200

    def test_reclassify_without_key_returns_401(self, client):
        r = client.post("/api/reclassify", json={"hashes": ["abc123"]})
        assert r.status_code == 401

    def test_config_put_without_key_returns_401(self, client):
        r = client.put("/api/config", json={"qbit_host": "http://new.host:8080"})
        assert r.status_code == 401

    def test_config_get_without_key_returns_401(self, client):
        r = client.get("/api/config")
        assert r.status_code == 401

    def test_config_get_with_valid_key_succeeds(self, client):
        r = client.get("/api/config", headers={"X-API-Key": "test-secret-key-123"})
        assert r.status_code == 200
        assert "qbit_username" in r.json()

    def test_delete_items_without_key_returns_401(self, client):
        r = client.delete("/api/items")
        assert r.status_code == 401

    def test_delete_items_broadcasts_items_cleared(self, client):
        events = []

        async def capture(event):
            events.append(event)

        client.app.state.ctx.core.bus.subscribe(EventType.ITEMS_CLEARED, capture)
        r = client.delete(
            "/api/items",
            headers={"X-API-Key": "test-secret-key-123"},
        )

        assert r.status_code == 200
        assert [event.type for event in events] == [EventType.ITEMS_CLEARED]

    def test_read_endpoints_still_open(self, client):
        """GET endpoints should remain unauthenticated for UI access."""
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/items").status_code == 200
        assert client.get("/api/health").status_code == 200

    def test_no_key_config_allows_all(self, client):
        """When API_KEY is empty, auth is disabled (backward compat)."""
        client.app.state.ctx.runtime.api_key = ""
        r = client.post("/api/crawl", json={"url": "https://example.com", "depth": 1})
        assert r.status_code == 200
