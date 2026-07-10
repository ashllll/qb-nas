"""
Test Magnet item lifecycle module event emission rules.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio

from magnet_harvester.bus import EventType, MessageBus
from magnet_harvester.transitions import (
    ClassificationTransitions,
    DiscoveryTransitions,
    DownloadTransitions,
)
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import AsyncItemStore, FakeStore


class RecordingBus(MessageBus):
    def __init__(self):
        super().__init__()
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def _lifecycle_modules(store, bus):
    return (
        ClassificationTransitions(store=store, bus=bus),
        DownloadTransitions(store=store, bus=bus),
    )


def _make_item(hash_key="ABC123", name="Test", status=TaskStatus.pending):
    return MagnetItem(
        hash=hash_key,
        name=name,
        magnet=f"magnet:?xt=urn:btih:{hash_key}",
        status=status,
    )


# ── 1. STORE_CHANGED is always emitted via classification_started ──


async def test_emit_item_changed_always_emits():
    """classification_started always broadcasts STORE_CHANGED."""
    store = FakeStore()
    bus = MessageBus()
    classification, downloads = _lifecycle_modules(AsyncItemStore(store), bus)

    item = _make_item("ABC123", "Test.Movie.2160p", TaskStatus.pending)
    store.add(item)

    events = []
    bus.subscribe(EventType.STORE_CHANGED, lambda e: events.append(e))

    await classification.started("ABC123")
    assert len(events) == 1
    assert events[0].type == EventType.STORE_CHANGED


async def test_discovery_clear_removes_items_and_emits_collection_event():
    backend = FakeStore()
    store = AsyncItemStore(backend)
    bus = RecordingBus()
    discovery = DiscoveryTransitions(store=store, bus=bus)
    backend.add(_make_item("CLEAR", status=TaskStatus.pending))

    count = await discovery.cleared()

    assert count == 1
    assert backend.count == 0
    assert [event.type for event in bus.events] == [EventType.ITEMS_CLEARED]


# ── 2. DOWNLOAD_RESULT: terminal always emits ──


async def test_terminal_status_always_emits_download_result():
    """Terminal statuses (success/error) always emit DOWNLOAD_RESULT via download_state_changed."""
    for terminal_status in (TaskStatus.success, TaskStatus.error):
        store = FakeStore()
        bus = MessageBus()
        classification, downloads = _lifecycle_modules(AsyncItemStore(store), bus)

        item = _make_item("TERM", "terminal", terminal_status)
        store.add(item)

        events = []
        bus.subscribe(EventType.DOWNLOAD_RESULT, lambda e: events.append(e))

        await downloads.state_changed("TERM", previous_status=TaskStatus.downloading)
        assert len(events) == 1, f"terminal {terminal_status} should emit"


# ── 3. DOWNLOAD_RESULT: new-phase emits ──


async def test_new_phase_emits_download_result():
    """Transitions from pending/adding/classifying/None emit DOWNLOAD_RESULT."""
    for prev in (TaskStatus.pending, TaskStatus.adding, TaskStatus.classifying, None):
        store = FakeStore()
        bus = MessageBus()
        classification, downloads = _lifecycle_modules(AsyncItemStore(store), bus)

        item = _make_item("NEW", "new phase", TaskStatus.queued)
        store.add(item)

        events = []
        bus.subscribe(EventType.DOWNLOAD_RESULT, lambda e: events.append(e))

        await downloads.state_changed("NEW", previous_status=prev)
        assert len(events) == 1, f"new phase from {prev} should emit"


# ── 4. DOWNLOAD_RESULT: routine oscillation does NOT emit ──


async def test_routine_oscillation_suppressed():
    """queued→downloading and back should NOT emit DOWNLOAD_RESULT (noise suppression)."""
    store = FakeStore()
    bus = MessageBus()
    classification, downloads = _lifecycle_modules(AsyncItemStore(store), bus)

    item = _make_item("OSC", "oscillating", TaskStatus.downloading)
    store.add(item)

    events = []
    bus.subscribe(EventType.DOWNLOAD_RESULT, lambda e: events.append(e))

    await downloads.state_changed("OSC", previous_status=TaskStatus.queued)
    assert len(events) == 0, "queued→downloading should be suppressed"

    item2 = MagnetItem(
        hash="OSC2",
        name="osc back",
        magnet="magnet:?xt=urn:btih:OSC2",
        status=TaskStatus.queued,
    )
    store.add(item2)
    events.clear()
    await downloads.state_changed("OSC2", previous_status=TaskStatus.downloading)
    assert len(events) == 0, "downloading→queued should be suppressed"


async def test_stale_completion_callbacks_do_not_overwrite_newer_state():
    """过期的分类/提交回调不能覆盖已推进的条目状态。"""
    store = FakeStore()
    bus = RecordingBus()
    classification, downloads = _lifecycle_modules(AsyncItemStore(store), bus)

    submitted = _make_item("SUBMITTED", status=TaskStatus.error)
    store.add(submitted)
    await downloads.submitted("SUBMITTED")
    assert store.get("SUBMITTED").status == TaskStatus.error
    assert bus.events == []

    classified = _make_item("CLASSIFIED", status=TaskStatus.pending)
    store.add(classified)
    await classification.classified("CLASSIFIED", {"category": "电影", "save_path": "/movies"})
    await classification.failed("CLASSIFIED", "stale callback")
    current = store.get("CLASSIFIED")
    assert current.status == TaskStatus.pending
    assert current.category is None
    assert current.error_msg is None
    assert bus.events == []


async def test_completion_callbacks_accept_their_expected_source_state():
    """adding/classifying 的正常完成路径保持不变。"""
    store = FakeStore()
    bus = RecordingBus()
    classification, downloads = _lifecycle_modules(AsyncItemStore(store), bus)

    submitted = _make_item("ADDING", status=TaskStatus.adding)
    store.add(submitted)
    await downloads.submitted("ADDING")
    assert store.get("ADDING").status == TaskStatus.queued

    classifying = _make_item("CLASSIFYING", status=TaskStatus.classifying)
    store.add(classifying)
    await classification.classified("CLASSIFYING", {"category": "电影", "save_path": "/movies"})
    current = store.get("CLASSIFYING")
    assert current.status == TaskStatus.pending
    assert current.category == "电影"


if __name__ == "__main__":
    asyncio.run(test_emit_item_changed_always_emits())
    asyncio.run(test_terminal_status_always_emits_download_result())
    asyncio.run(test_new_phase_emits_download_result())
    asyncio.run(test_routine_oscillation_suppressed())
    print("=== Magnet item lifecycle event tests passed! ===")
