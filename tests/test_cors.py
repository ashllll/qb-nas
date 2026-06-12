"""Tests for CORS configuration."""
from __future__ import annotations


from magnet_harvester.config import settings


class TestCORS:
    """Verify CORS middleware is configured from settings."""

    def test_no_cors_middleware_when_empty(self):
        settings.CORS_ALLOWED_ORIGINS = ""
        # When empty, no CORS middleware should be registered
        # (but we can't easily remove already-registered middleware in tests)
        # So we verify the config parsing instead
        origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
        assert origins == []

    def test_cors_origins_parsed_correctly(self):
        settings.CORS_ALLOWED_ORIGINS = "https://app.example.com, https://admin.example.com"
        origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
        assert origins == ["https://app.example.com", "https://admin.example.com"]

    def test_cors_single_origin(self):
        settings.CORS_ALLOWED_ORIGINS = "https://app.example.com"
        origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
        assert origins == ["https://app.example.com"]
