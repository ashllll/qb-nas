"""Tests for API key authentication middleware."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magnet_harvester.bus import EventType
from magnet_harvester.main import app
from magnet_harvester.config import settings


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestAPIKeyAuth:
    """Verify sensitive endpoints require API key."""

    @pytest.fixture(autouse=True)
    def _set_api_key(self):
        original = getattr(settings, "API_KEY", None)
        settings.API_KEY = "test-secret-key-123"
        yield
        settings.API_KEY = original

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

    def test_delete_items_without_key_returns_401(self, client):
        r = client.delete("/api/items")
        assert r.status_code == 401

    def test_delete_items_broadcasts_items_cleared(self, client):
        events = []

        async def capture(event):
            events.append(event)

        client.app.state.ctx.bus.subscribe(EventType.ITEMS_CLEARED, capture)
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
        assert client.get("/api/config").status_code == 200

    def test_no_key_config_allows_all(self, client):
        """When API_KEY is empty, auth is disabled (backward compat)."""
        settings.API_KEY = ""
        r = client.post("/api/crawl", json={"url": "https://example.com", "depth": 1})
        assert r.status_code == 200
