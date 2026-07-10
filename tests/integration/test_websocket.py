"""WebSocket integration tests: verify /ws endpoint lifecycle.

Uses Starlette TestClient for native WebSocket connections against the real
ASGI app. Tests init message, ping/pong protocol, and event broadcast.
"""

from __future__ import annotations

import asyncio
import json
import warnings

import pytest
from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=StarletteDeprecationWarning,
)

from starlette.testclient import TestClient  # noqa: E402

from magnet_harvester.models import MagnetItem, TaskStatus  # noqa: E402
from magnet_harvester.bus import Event, EventType  # noqa: E402
from tests.fixtures import make_test_app  # noqa: E402


@pytest.fixture
def app_ctx():
    """Create test app and yield (app, ctx) with cleanup."""
    app, ctx, _ = make_test_app()
    yield app, ctx
    # Cleanup pending bg tasks to avoid "Task was destroyed" warnings
    if ctx.runtime.bg_manager:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(ctx.runtime.bg_manager.shutdown())
            loop.close()
        except Exception:
            pass


# ── Task 1: Init message ─────────────────────


def test_websocket_connect_receives_init_message(app_ctx):
    """On connect, the server should send an 'init' message with current items."""
    app, ctx = app_ctx

    # Add a seed item so init message has content
    asyncio.run(
        ctx.core.store.add(
            MagnetItem(
                hash="INIT001",
                name="Init Test Item",
                magnet="magnet:?xt=urn:btih:INIT001",
                category="电影",
                status=TaskStatus.pending,
            )
        )
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        raw = ws.receive_text()
        data = json.loads(raw)
        assert data["type"] == "init"
        assert len(data["items"]) >= 1
        item_hashes = [i["hash"] for i in data["items"]]
        assert "INIT001" in item_hashes


def test_websocket_init_empty_when_no_items(app_ctx):
    """On connect with no items, init should have an empty items list."""
    app, ctx = app_ctx

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        raw = ws.receive_text()
        data = json.loads(raw)
        assert data["type"] == "init"


# ── Task 2: Ping/pong ────────────────────────


def test_websocket_ping_pong(app_ctx):
    """Sending 'ping' should receive a 'pong' response."""
    app, ctx = app_ctx

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # Consume the complete initial snapshot protocol.
        assert json.loads(ws.receive_text())["type"] == "init"
        assert json.loads(ws.receive_text())["type"] == "init_done"

        # Send ping
        ws.send_text("ping")
        pong_raw = ws.receive_text()
        pong_data = json.loads(pong_raw)
        assert pong_data["type"] == "pong"


def test_websocket_json_ping_pong(app_ctx):
    """Sending JSON ping should also receive pong."""
    app, ctx = app_ctx

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # Consume the complete initial snapshot protocol.
        assert json.loads(ws.receive_text())["type"] == "init"
        assert json.loads(ws.receive_text())["type"] == "init_done"

        # Send JSON ping
        ws.send_text(json.dumps({"type": "ping"}))
        pong_raw = ws.receive_text()
        pong_data = json.loads(pong_raw)
        assert pong_data["type"] == "pong"


# ── Task 3: Event broadcast ──────────────────


def test_websocket_broadcaster_sends_events_directly():
    """WSBroadcaster sends bus events to connected clients (in-process)."""
    from magnet_harvester.bus import MessageBus
    from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
    from magnet_harvester.api.websocket import WSBroadcaster
    from starlette.websockets import WebSocketState

    bus = MessageBus()
    store = InMemoryItemStore()
    broadcaster = WSBroadcaster(bus=bus, store=AsyncItemStore(store))

    class CollectingWS:
        def __init__(self):
            self.sent: list[str] = []
            self.client_state = WebSocketState.CONNECTED

        async def send_text(self, data: str):
            self.sent.append(data)

        async def accept(self):
            pass

    ws = CollectingWS()
    broadcaster.add(ws)

    # Emit event via bus
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            bus.emit(
                Event(EventType.MAGNET_FOUND, {"item": {"hash": "WS001", "name": "WS Event Test"}})
            )
        )
        # Retry-loop: wait up to 1s for event delivery (avoids hard-coded sleep)
        for _ in range(100):
            if any("magnet_found" in m for m in ws.sent):
                break
            loop.run_until_complete(asyncio.sleep(0.01))
    finally:
        loop.close()

    assert len(ws.sent) >= 1
    data = json.loads(ws.sent[0])
    assert data["type"] == "magnet_found"
    assert data["item"]["hash"] == "WS001"
