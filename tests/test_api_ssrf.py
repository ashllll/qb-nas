"""Integration tests for SSRF protection at API level."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from magnet_harvester.main import app
from tests._client import asgi_client


@pytest.fixture
def client(monkeypatch):
    from magnet_harvester.config import settings

    monkeypatch.setattr(settings, "ALLOW_INSECURE_WRITE_API", True)
    with asgi_client(app) as c:
        yield c


class TestCrawlSSRFProtection:
    """Verify /api/crawl rejects unsafe URLs."""

    def test_rejects_localhost_url(self, client):
        r = client.post("/api/crawl", json={"url": "http://localhost:8080", "depth": 1})
        assert r.status_code == 422
        assert "private" in r.text.lower() or "localhost" in r.text.lower()

    def test_rejects_192_168_url(self, client):
        r = client.post("/api/crawl", json={"url": "http://192.168.1.1:8080", "depth": 1})
        assert r.status_code == 422
        assert "private" in r.text.lower()

    def test_rejects_file_protocol(self, client):
        r = client.post("/api/crawl", json={"url": "file:///etc/passwd", "depth": 1})
        assert r.status_code == 422

    def test_accepts_valid_public_url(self, client):
        client.app.state.ctx.pipeline.admit_crawl_target = AsyncMock(
            return_value="https://example.com"
        )
        r = client.post("/api/crawl", json={"url": "https://example.com", "depth": 1})
        assert r.status_code == 200
