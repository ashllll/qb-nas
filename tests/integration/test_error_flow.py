"""Integration tests: error handling through the pipeline API."""

from __future__ import annotations

from tests.fixtures import make_test_app, asgi_client
from magnet_harvester.errors import ErrorCategory, ErrorSeverity


def test_error_endpoint_returns_errors():
    """GET /api/errors should return recorded errors from the handler."""
    app, ctx, _ = make_test_app()
    handler = ctx.error_handler

    handler.record(
        category=ErrorCategory.CRAWLER,
        severity=ErrorSeverity.ERROR,
        message="simulated error for test",
    )

    with asgi_client(app) as client:
        resp = client.get("/api/errors")

    assert resp.status_code == 200
    data = resp.json()
    assert "errors" in data
    assert "stats" in data


def test_multiple_error_occurrences_accumulate_count():
    """Repeated errors should count up in the aggregate stats."""
    app, ctx, _ = make_test_app()
    handler = ctx.error_handler

    for _ in range(3):
        handler.record(
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.WARNING,
            message="connection timeout",
            details={"host": "example.com"},
        )

    stats = handler.get_error_stats()
    assert stats["total_errors"] >= 3


def test_error_clear_all():
    """Clearing all errors should remove them from handler state."""
    app, ctx, _ = make_test_app()
    handler = ctx.error_handler

    handler.record(
        category=ErrorCategory.CONFIG,
        severity=ErrorSeverity.ERROR,
        message="bad config",
    )
    assert handler.get_error_stats()["total_errors"] >= 1

    handler.clear_all()
    stats = handler.get_error_stats()
    assert stats["total_errors"] == 0, f"expected 0 after clear, got {stats['total_errors']}"
