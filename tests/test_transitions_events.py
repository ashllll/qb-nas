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


# ── 6. reconcile_snapshot 透传 qB 异常状态为可读 error_msg ──


@pytest.mark.asyncio
async def test_reconcile_snapshot_propagates_qbit_error_message():
    """qB 侧 missingFiles 必须落到 error_msg，DOWNLOAD_RESULT 事件携带真实原因。"""
    store = _make_store()
    bus = RecordingBus()
    transitions = DownloadTransitions(store=store, bus=bus)
    await store.add(
        MagnetItem(
            hash="QBITERR1",
            name="Missing Files Item",
            magnet="magnet:?xt=urn:btih:QBITERR1",
            status=TaskStatus.queued,
        )
    )
    item = await store.get("QBITERR1")

    changed = await transitions.reconcile_snapshot(
        "QBITERR1",
        item,
        {"state": "missingFiles", "progress": 0.4},
        was_removed=False,
    )

    assert changed is True
    updated = await store.get("QBITERR1")
    assert updated.status == TaskStatus.error
    assert updated.error_msg == "qB 种子状态异常: missingFiles"
    event = _event(bus, EventType.DOWNLOAD_RESULT)
    assert event is not None
    assert event.data["status"] == "error"
    assert event.data["error_msg"] == "qB 种子状态异常: missingFiles"


@pytest.mark.asyncio
async def test_reconcile_snapshot_same_error_does_not_reemit():
    """连续两轮相同的异常快照不产生字段变化，不重复发射事件刷屏。"""
    store = _make_store()
    bus = RecordingBus()
    transitions = DownloadTransitions(store=store, bus=bus)
    await store.add(
        MagnetItem(
            hash="QBITERR2",
            name="Repeated Error Item",
            magnet="magnet:?xt=urn:btih:QBITERR2",
            status=TaskStatus.queued,
        )
    )

    snapshot = {"state": "missingFiles", "progress": 0.4}
    item = await store.get("QBITERR2")
    first = await transitions.reconcile_snapshot("QBITERR2", item, snapshot, was_removed=False)
    item = await store.get("QBITERR2")
    second = await transitions.reconcile_snapshot("QBITERR2", item, snapshot, was_removed=False)

    assert first is True
    assert second is False
    results = [e for e in bus.events if e.type == EventType.DOWNLOAD_RESULT]
    assert len(results) == 1


@pytest.mark.asyncio
async def test_reconcile_snapshot_clears_error_message_on_recovery():
    """qB 状态从异常恢复到下载中时，error_msg 必须被清除。"""
    store = _make_store()
    bus = RecordingBus()
    transitions = DownloadTransitions(store=store, bus=bus)
    await store.add(
        MagnetItem(
            hash="QBITREC1",
            name="Recovering Item",
            magnet="magnet:?xt=urn:btih:QBITREC1",
            status=TaskStatus.error,
            error_msg="qB 种子状态异常: missingFiles",
        )
    )
    item = await store.get("QBITREC1")

    changed = await transitions.reconcile_snapshot(
        "QBITREC1",
        item,
        {"state": "downloading", "progress": 0.5},
        was_removed=False,
    )

    assert changed is True
    updated = await store.get("QBITREC1")
    assert updated.status == TaskStatus.downloading
    assert updated.error_msg is None
