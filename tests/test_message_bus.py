"""
Test MessageBus - deterministic concurrent delivery and failure isolation.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.bus import Event, EventType, MessageBus


@pytest.mark.asyncio
async def test_emit_waits_for_subscribers_to_finish():
    bus = MessageBus()
    seen = []

    async def slow_subscriber(event):
        await asyncio.sleep(0.01)
        seen.append(event.type.value)

    bus.subscribe(EventType.CRAWL_DONE, slow_subscriber)

    await bus.emit(Event(EventType.CRAWL_DONE, {"total": 1}))

    assert seen == ["crawl_done"]


@pytest.mark.asyncio
async def test_emit_isolates_subscriber_failures_and_still_delivers():
    bus = MessageBus()
    seen = []

    async def bad_subscriber(_event):
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")

    async def good_subscriber(event):
        await asyncio.sleep(0.01)
        seen.append(event.type.value)

    bus.subscribe(EventType.STORE_CHANGED, bad_subscriber)
    bus.subscribe(EventType.STORE_CHANGED, good_subscriber)

    await bus.emit(Event(EventType.STORE_CHANGED, {"item": {"hash": "A"}}))

    assert seen == ["store_changed"]
