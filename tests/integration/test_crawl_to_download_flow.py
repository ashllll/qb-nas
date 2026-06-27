"""Integration tests for the API-driven crawl pipeline.

Uses shared test fixtures from tests.fixtures.
"""

from __future__ import annotations

import asyncio

from tests.fixtures import make_test_app, asgi_client


def _wait_for_task(client, bg_manager, task_id, max_iters=20):
    """Poll the background task until it finishes, using the client's event loop."""
    for _ in range(max_iters):
        client._loop.run_until_complete(asyncio.sleep(0.01))
        snapshot = bg_manager.get_task(task_id)
        if snapshot and snapshot["status"] != "running":
            return snapshot
    return bg_manager.get_task(task_id)


def test_api_crawl_auto_download_flow_reaches_qbit_and_items_view():
    """Full pipeline: POST /api/crawl → crawl → classify → auto-download → items listable."""
    app, ctx, qbit = make_test_app()

    with asgi_client(app) as client:
        started = client.post(
            "/api/crawl",
            json={"url": "https://example.com/source", "depth": 2, "auto_download": True},
        )
        assert started.status_code == 200
        task_id = started.json()["task_id"]

        snapshot = _wait_for_task(client, ctx.bg_manager, task_id)
        assert snapshot["status"] == "completed"

        listed = client.get("/api/items")

    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["category"] == "电影"
    assert payload["items"][0]["status"] == "queued"
    assert qbit.added == [("magnet:?xt=urn:btih:ABCDEF1234567890", "电影", "电影")]


def test_crawl_without_auto_download_does_not_call_qbit():
    """Crawl with auto_download=false should classify items but not submit to qB."""
    app, ctx, qbit = make_test_app()

    with asgi_client(app) as client:
        started = client.post(
            "/api/crawl",
            json={"url": "https://example.com/movie", "depth": 1, "auto_download": False},
        )
        assert started.status_code == 200
        task_id = started.json()["task_id"]

        snapshot = _wait_for_task(client, ctx.bg_manager, task_id)
        assert snapshot["status"] == "completed"

        listed = client.get("/api/items")

    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["status"] == "pending"  # classified but not submitted
    assert qbit.added == []  # qB never called
