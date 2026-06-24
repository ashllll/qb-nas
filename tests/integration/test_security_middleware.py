"""Security middleware integration tests: API Key auth, URL validation, CORS."""

from __future__ import annotations

import pytest

from magnet_harvester.models import MagnetItem, TaskStatus
from tests.fixtures import make_test_app, asgi_client


@pytest.fixture
def app_with_key():
    """Create a test app with API Key auth enabled."""
    store = None
    app, ctx, qbit = make_test_app(store=store)
    ctx.api_key = "secret"
    # Seed a pending item
    ctx.store.add(MagnetItem(
        hash="ALPHA1234567890",
        name="Test.Item.2160p",
        magnet="magnet:?xt=urn:btih:ALPHA1234567890",
        status=TaskStatus.pending,
    ))
    return app, ctx, qbit


class TestAPIKeyAuth:
    def test_write_endpoint_requires_api_key(self, app_with_key):
        """POST /api/crawl without X-API-Key should return 401."""
        app, _, _ = app_with_key
        with asgi_client(app) as client:
            resp = client.post("/api/crawl", json={"url": "https://example.com/test", "depth": 1})
        assert resp.status_code == 401

    def test_write_endpoint_succeeds_with_correct_api_key(self, app_with_key):
        """POST /api/crawl with correct X-API-Key should succeed."""
        app, _, _ = app_with_key
        with asgi_client(app) as client:
            resp = client.post(
                "/api/crawl", json={"url": "https://example.com/test", "depth": 1},
                headers={"X-API-Key": "secret"},
            )
        assert resp.status_code == 200

    def test_write_endpoint_fails_with_wrong_api_key(self, app_with_key):
        """POST /api/crawl with wrong X-API-Key should return 401."""
        app, _, _ = app_with_key
        with asgi_client(app) as client:
            resp = client.post(
                "/api/crawl", json={"url": "https://example.com/test", "depth": 1},
                headers={"X-API-Key": "wrong-key"},
            )
        assert resp.status_code == 401

    def test_read_endpoint_does_not_require_api_key(self, app_with_key):
        """GET /api/items should work without API Key."""
        app, _, _ = app_with_key
        with asgi_client(app) as client:
            resp = client.get("/api/items")
        assert resp.status_code == 200


class TestURLValidation:
    @pytest.fixture(autouse=True)
    def _setup(self, app_with_key):
        self.app, _, _ = app_with_key

    def test_crawl_invalid_url_returns_422(self):
        """POST /api/crawl with invalid URL should return 422."""
        with asgi_client(self.app) as client:
            resp = client.post(
                "/api/crawl", json={"url": "not-a-valid-url", "depth": 1},
                headers={"X-API-Key": "secret"},
            )
        assert resp.status_code == 422

    def test_crawl_empty_url_rejected(self):
        """POST /api/crawl with empty URL should return 422."""
        with asgi_client(self.app) as client:
            resp = client.post(
                "/api/crawl", json={"url": "", "depth": 1},
                headers={"X-API-Key": "secret"},
            )
        assert resp.status_code == 422
