"""Integration tests for SSRF protection at API level."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magnet_harvester.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
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
        # We can't actually crawl, but validation should pass
        r = client.post("/api/crawl", json={"url": "https://example.com", "depth": 1})
        # 200 = started (background task), 422 = validation error
        assert r.status_code in (200, 422)
        if r.status_code == 422:
            # If it fails, it should NOT be due to SSRF rules
            assert "private" not in r.text.lower()
            assert "unsupported protocol" not in r.text.lower()
