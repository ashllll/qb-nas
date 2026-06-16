"""
Test MessageBus - deterministic concurrent delivery and failure isolation.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.bus import Event, EventType, MessageBus, NullBus


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
async def test_emit_isolates_subscriber_failures_and_still_delivers(caplog):
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

    with caplog.at_level("DEBUG"):
        await bus.emit(Event(EventType.STORE_CHANGED, {"item": {"hash": "A"}}))

    assert seen == ["store_changed"]
    assert "订阅者异常" in caplog.text


@pytest.mark.asyncio
async def test_global_and_typed_subscribers_both_invoked():
    bus = MessageBus()
    received = []

    async def global_subscriber(event):
        received.append(("global", event.type.value))

    async def typed_subscriber(event):
        received.append(("typed", event.type.value))

    bus.subscribe(None, global_subscriber)
    bus.subscribe(EventType.CRAWL_DONE, typed_subscriber)

    await bus.emit(Event(EventType.CRAWL_DONE, {"total": 1}))

    assert ("global", "crawl_done") in received
    assert ("typed", "crawl_done") in received


@pytest.mark.asyncio
async def test_global_subscriber_alone_receives_untyped_event():
    bus = MessageBus()
    received = []

    async def global_subscriber(event):
        received.append(event.type.value)

    async def typed_subscriber(event):
        received.append(f"typed:{event.type.value}")

    bus.subscribe(None, global_subscriber)
    bus.subscribe(EventType.CRAWL_DONE, typed_subscriber)

    await bus.emit(Event(EventType.STORE_CHANGED, {"item": {"hash": "A"}}))

    assert received == ["store_changed"]


@pytest.mark.asyncio
async def test_unsubscribe_removes_subscriber():
    bus = MessageBus()
    received = []

    async def subscriber(event):
        received.append(event.type.value)

    bus.subscribe(EventType.CRAWL_DONE, subscriber)
    await bus.emit(Event(EventType.CRAWL_DONE, {"total": 1}))

    bus.unsubscribe(EventType.CRAWL_DONE, subscriber)
    await bus.emit(Event(EventType.CRAWL_DONE, {"total": 2}))

    assert received == ["crawl_done"]


@pytest.mark.asyncio
async def test_unsubscribe_global_removes_global_subscriber():
    bus = MessageBus()
    received = []

    async def global_subscriber(event):
        received.append(event.type.value)

    bus.subscribe(None, global_subscriber)
    await bus.emit(Event(EventType.STORE_CHANGED, {"test": 1}))

    bus.unsubscribe(None, global_subscriber)
    await bus.emit(Event(EventType.STORE_CHANGED, {"test": 2}))

    assert received == ["store_changed"]


@pytest.mark.asyncio
async def test_null_bus_emits_nothing():
    bus = NullBus()
    received = []

    async def subscriber(event):
        received.append(event.type.value)

    bus.subscribe(EventType.CRAWL_DONE, subscriber)
    bus.subscribe(None, subscriber)

    await bus.emit(Event(EventType.CRAWL_DONE, {"total": 1}))

    assert received == []
