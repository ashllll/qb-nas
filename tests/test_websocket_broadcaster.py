"""
Test WSBroadcaster — WebSocket event broadcast service.
"""
import sys
import os
import asyncio
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.bus import MessageBus, Event, EventType
from magnet_harvester.api.websocket import WSBroadcaster


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send_text(self, data: str):
        self.sent.append(data)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_subscribes_to_bus_on_init():
    bus = MessageBus()
    broadcaster = WSBroadcaster(bus=bus)

    # WSBroadcaster should have subscribed to bus
    assert len(bus._global_subscribers) == 1


@pytest.mark.asyncio
async def test_broadcasts_event_to_all_connected_ws():
    bus = MessageBus()
    broadcaster = WSBroadcaster(bus=bus)

    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket()
    broadcaster.add(ws1)
    broadcaster.add(ws2)

    event = Event(EventType.MAGNET_FOUND, {"item": {"hash": "ABC", "name": "Test"}})
    await bus.emit(event)

    # Wait for async broadcast
    await asyncio.sleep(0.05)

    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    data = json.loads(ws1.sent[0])
    assert data["type"] == "magnet_found"


@pytest.mark.asyncio
async def test_removes_dead_ws_on_broadcast_failure():
    bus = MessageBus()
    broadcaster = WSBroadcaster(bus=bus)

    good_ws = FakeWebSocket()
    bad_ws = FakeWebSocket()

    # Make bad_ws raise on send
    async def raise_on_send(_):
        raise RuntimeError("Connection closed")

    bad_ws.send_text = raise_on_send

    broadcaster.add(good_ws)
    broadcaster.add(bad_ws)

    event = Event(EventType.STORE_CHANGED, {"item": {}})
    await bus.emit(event)
    await asyncio.sleep(0.05)

    # bad_ws should be removed, good_ws should still receive
    assert len(good_ws.sent) == 1


@pytest.mark.asyncio
async def test_send_init_sends_all_items():
    bus = MessageBus()
    broadcaster = WSBroadcaster(bus=bus)

    ws = FakeWebSocket()
    items = [
        {"hash": "AAA", "name": "Movie 1", "status": "pending"},
        {"hash": "BBB", "name": "Movie 2", "status": "classified"},
    ]
    await broadcaster.send_init(ws, items)

    assert len(ws.sent) == 1
    data = json.loads(ws.sent[0])
    assert data["type"] == "init"
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_remove_disconnects_ws():
    bus = MessageBus()
    broadcaster = WSBroadcaster(bus=bus)

    ws = FakeWebSocket()
    broadcaster.add(ws)
    broadcaster.remove(ws)

    event = Event(EventType.CRAWL_DONE, {"total": 5})
    await bus.emit(event)
    await asyncio.sleep(0.05)

    assert len(ws.sent) == 0
