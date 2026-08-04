"""
Test event versioning — CLASSIFY_DONE / DOWNLOAD_RESULT / STORE_CHANGED
carry updated_at so the frontend can drop late-arriving stale events.
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import (
    ClassificationTransitions,
    DownloadTransitions,
)


class RecordingBus(MessageBus):
    """捕获所有事件的测试总线（不触发订阅回调）。"""

    def __init__(self):
        super().__init__()
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _make_store():
    return AsyncItemStore(InMemoryItemStore())


def _event(bus: RecordingBus, event_type: EventType) -> Event | None:
    return next((e for e in bus.events if e.type == event_type), None)


# ── 1. classify_done 携带 updated_at ──


@pytest.mark.asyncio
async def test_classify_done_carries_updated_at():
    store = _make_store()
    bus = RecordingBus()
    transitions = ClassificationTransitions(store=store, bus=bus)
    await store.add(
        MagnetItem(
            hash="EVT001",
            name="Event Test",
            magnet="magnet:?xt=urn:btih:EVT001",
            status=TaskStatus.classifying,
        )
    )

    await transitions.classified("EVT001", {"category": "电影", "confidence": "high"})

    event = _event(bus, EventType.CLASSIFY_DONE)
    assert event is not None
    assert event.data["hash"] == "EVT001"
    assert event.data["category"] == "电影"
    assert isinstance(event.data["updated_at"], str)
    assert event.data["updated_at"]


@pytest.mark.asyncio
async def test_classify_done_updated_at_is_after_classify_start():
    store = _make_store()
    bus = RecordingBus()
    transitions = ClassificationTransitions(store=store, bus=bus)
    await store.add(
        MagnetItem(
            hash="EVT001",
            name="Event Test",
            magnet="magnet:?xt=urn:btih:EVT001",
            status=TaskStatus.classifying,
        )
    )

    await transitions.classified("EVT001", {"category": "电影"})

    event = _event(bus, EventType.CLASSIFY_DONE)
    item = await store.get("EVT001")
    assert event.data["updated_at"] == item.updated_at.isoformat()


@pytest.mark.asyncio
async def test_manually_classified_carries_updated_at():
    store = _make_store()
    bus = RecordingBus()
    transitions = ClassificationTransitions(store=store, bus=bus)
    await store.add(
        MagnetItem(
            hash="EVT001",
            name="Event Test",
            magnet="magnet:?xt=urn:btih:EVT001",
        )
    )

    ok = await transitions.manually_classified("EVT001", "动漫")
    assert ok is True

    event = _event(bus, EventType.CLASSIFY_DONE)
    assert event is not None
    assert event.data["category"] == "动漫"
    assert isinstance(event.data["updated_at"], str)


# ── 2. download_result 携带 updated_at ──


@pytest.mark.asyncio
async def test_download_result_carries_updated_at():
    store = _make_store()
    bus = RecordingBus()
    transitions = DownloadTransitions(store=store, bus=bus)
    await store.add(
        MagnetItem(
            hash="EVT001",
            name="Event Test",
            magnet="magnet:?xt=urn:btih:EVT001",
            status=TaskStatus.pending,
        )
    )

    assert await transitions.submitting("EVT001") is True
    await transitions.submitted("EVT001")

    event = _event(bus, EventType.DOWNLOAD_RESULT)
    assert event is not None
    assert event.data["hash"] == "EVT001"
    assert isinstance(event.data["updated_at"], str)
    assert event.data["updated_at"]


# ── 3. store_changed 携带 updated_at（完整 item 序列化） ──


@pytest.mark.asyncio
async def test_store_changed_item_carries_updated_at():
    store = _make_store()
    bus = RecordingBus()
    transitions = ClassificationTransitions(store=store, bus=bus)
    await store.add(
        MagnetItem(
            hash="EVT001",
            name="Event Test",
            magnet="magnet:?xt=urn:btih:EVT001",
        )
    )

    await transitions.manually_classified("EVT001", "音乐")

    event = _event(bus, EventType.STORE_CHANGED)
    assert event is not None
    assert event.data["item"]["updated_at"]  # 非空
    # WebSocket 广播经 _json_serializer 转为 ISO 字符串，前端据此做版本比较
    from magnet_harvester.api.websocket import _json_serializer

    serialized = json.loads(json.dumps(event.data, default=_json_serializer))
    assert isinstance(serialized["item"]["updated_at"], str)
    assert serialized["item"]["updated_at"]
