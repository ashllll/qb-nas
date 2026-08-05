# WebSocket 集成测试 Implementation Plan

> **状态：已完成（2026-06-24）**。对应实现为 `tests/integration/test_websocket.py`（真实 ASGI WebSocket 生命周期测试）与 `tests/integration/test_websocket_auth.py`（API Key 握手认证）。本文件保留为计划记录。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the misnamed `test_websocket.py` (which tests HTTP crawl, not WebSocket) with a proper WebSocket integration test that exercises the full ASGI WebSocket lifecycle.

**Architecture:** Use Starlette's `TestClient.websocket_connect()` to establish real WebSocket connections against the FastAPI app. Connect→receive `init`→send `ping`→receive `pong`→trigger crawl via HTTP→verify `magnet_found` event arrives via WS. Reuse `tests/fixtures.py` `make_test_app()` for test app setup.

**Tech Stack:** Starlette TestClient (built-in WebSocket support), FastAPI, WSBroadcaster, httpx ASGI transport

**Problem:** `tests/integration/test_websocket.py` currently opens an `httpx.AsyncClient` (pure HTTP), never connects to the `/ws` endpoint, and only verifies item storage via `GET /api/items`. The test name is misleading — no WebSocket events are ever received or verified.

---

### Task 1: Write failing WebSocket connection test

**Files:**

- Replace: `tests/integration/test_websocket.py` (full rewrite)
- Reuse: `tests/fixtures.py` `make_test_app()`

**Step 1: Rewrite test file with proper WS connect test**

```python
"""WebSocket integration tests: verify event broadcast via /ws endpoint.

Uses Starlette TestClient for native WebSocket support.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from tests.fixtures import make_test_app


def test_websocket_connect_receives_init_message():
    """On connect, the server should send an 'init' message with current items."""
    app, ctx, _ = make_test_app()

    # Add a seed item so init message has content
    from magnet_harvester.models import MagnetItem, TaskStatus
    ctx.store.add(MagnetItem(
        hash="INIT001", name="Init Test Item", magnet="magnet:?xt=urn:btih:INIT001",
        category="电影", status=TaskStatus.pending,
    ))

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # First message should be init
        raw = ws.receive_text()
        data = json.loads(raw)
        assert data["type"] == "init"
        assert len(data["items"]) >= 1
        item_hashes = [i["hash"] for i in data["items"]]
        assert "INIT001" in item_hashes
```

**Step 2: Run it — should fail because Starlette deprecation warning might break TestClient**

Run: `python -m pytest tests/integration/test_websocket.py::test_websocket_connect_receives_init_message -v`
Expected: PASS (TestClient works despite deprecation) — if it fails due to import/organization, adjust

**Step 3: Commit**

```bash
git add tests/integration/test_websocket.py
git commit -m "test: add WebSocket init message integration test"
```

---

### Task 2: Ping/pong WebSocket test

**Files:**

- Modify: `tests/integration/test_websocket.py`

**Step 1: Add ping/pong test**

```python
def test_websocket_ping_pong():
    """Sending 'ping' should receive a 'pong' response."""
    app, ctx, _ = make_test_app()

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # Consume init message
        init_raw = ws.receive_text()
        init_data = json.loads(init_raw)
        assert init_data["type"] == "init"

        # Send ping
        ws.send_text("ping")
        pong_raw = ws.receive_text()
        pong_data = json.loads(pong_raw)
        assert pong_data["type"] == "pong"
```

**Step 2: Run**

Run: `python -m pytest tests/integration/test_websocket.py::test_websocket_ping_pong -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/integration/test_websocket.py
git commit -m "test: add WebSocket ping/pong integration test"
```

---

### Task 3: Event broadcast via WebSocket test

**Files:**

- Modify: `tests/integration/test_websocket.py`

**Step 1: Add crawl event broadcast test**

Verifies that when a crawl runs, the MAGNET_FOUND events are broadcast to connected WS clients.

```python
def test_websocket_receives_magnet_found_on_crawl():
    """After a crawl, WebSocket client should receive MAGNET_FOUND events."""
    app, ctx, _ = make_test_app()

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # Consume init message
        init_raw = ws.receive_text()
        assert json.loads(init_raw)["type"] == "init"

        # Trigger crawl via HTTP (TestClient supports both WS and HTTP)
        crawl_resp = client.post(
            "/api/crawl",
            json={"url": "https://example.com/test", "depth": 1},
        )
        assert crawl_resp.status_code == 200

        # Wait for and collect WS messages
        # After the crawl, we expect: crawl_start, magnet_found(crawl_progress, etc.)
        # At minimum, we should see magnet_found or crawl_done
        found_magnet = False
        for _ in range(20):
            try:
                raw = ws.receive_text(timeout=0.5)
            except Exception:
                break
            data = json.loads(raw)
            if data.get("type") == "magnet_found":
                found_magnet = True
                assert "hash" in data.get("item", {}) or data.get("item", {}).get("name")
                break
            if data.get("type") == "crawl_done":
                # crawl finished without magnet_found? check total
                break

        assert found_magnet, "Expected magnet_found event via WebSocket"
```

**Step 2: Run**

Run: `python -m pytest tests/integration/test_websocket.py::test_websocket_receives_magnet_found_on_crawl -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/integration/test_websocket.py
git commit -m "test: add WebSocket event broadcast integration test"
```

---

### Task 4: Remove old crawl test from test_websocket.py

The current file also has a leftover crawl-via-HTTP test that was the original `test_websocket_receives_magnet_found_event`. The new test in Task 3 covers this better (verifies WS messages). The old one should be removed.

**Files:**

- Modify: `tests/integration/test_websocket.py`

**Step 1: Verify no test in test_websocket.py remains that doesn't use WebSocket**

The file should contain only tests that open a `client.websocket_connect("/ws")` context.

**Step 2: Run full integration suite**

Run: `python -m pytest tests/integration/ -v`
Expected: All tests pass, no regressions

**Step 3: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: All tests pass

**Step 4: Commit**

```bash
git add tests/integration/test_websocket.py
git commit -m "test: clean up test_websocket.py, remove non-WS tests"
```

---

### Task 5: Clean up leftover bg task warning

If the TestClient leaves pending asyncio tasks, add a fixture cleanup that waits for bg tasks to complete.

**Files:**

- Modify: `tests/integration/test_websocket.py`

**Step 1: Add fixture or context manager to cleanup after WebSocket tests**

```python
@pytest.fixture
def app_and_ctx():
    """Create test app and yield (app, ctx) with cleanup."""
    app, ctx, _ = make_test_app()
    yield app, ctx
    # Cleanup: ensure bg tasks complete
    if ctx.bg_manager:
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            loop.run_until_complete(ctx.bg_manager.shutdown())
            loop.close()
        except Exception:
            pass
```

**Step 2: Rewrite tests to use fixture**

**Step 3: Run**

Run: `python -m pytest tests/integration/test_websocket.py -v`
Expected: PASS, no "Task was destroyed but it is pending" warnings

**Step 4: Commit**

```bash
git add tests/integration/test_websocket.py
git commit -m "test: add WebSocket test cleanup fixture"
```

---

### Execution Handoff

**Plan complete and saved to `docs/plans/2026-06-24-websocket-integration-test.md`.** Two execution options:

**1. Subagent-Driven (this session)** — I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
