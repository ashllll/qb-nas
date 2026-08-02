"""
Test MagnetItemTransitions event emission rules (ItemEventEmitter inlined).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import threading

from magnet_harvester.bus import EventType, MessageBus
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import FakeStore


class RecordingBus(MessageBus):
    def __init__(self):
        super().__init__()
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class CoordinatedCASStore(FakeStore):
    """让两个 CAS 调用同时竞争，并记录 transition 是否绕过原子接口。"""

    blocks_event_loop = True

    def __init__(self):
        super().__init__()
        self._cas_barrier = threading.Barrier(2)
        self.get_calls = 0
        self.cas_calls = 0
        self.operations = []

    def get(self, hash_key: str):
        self.get_calls += 1
        self.operations.append("get")
        return super().get(hash_key)

    def update_if_status(self, hash_key, expected_statuses, **fields):
        self.cas_calls += 1
        self.operations.append("cas")
        self._cas_barrier.wait(timeout=2)
        return super().update_if_status(hash_key, expected_statuses, **fields)


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
    transitions = MagnetItemTransitions(store=store, bus=bus)

    item = _make_item("ABC123", "Test.Movie.2160p", TaskStatus.pending)
    store.add(item)

    events = []
    bus.subscribe(EventType.STORE_CHANGED, lambda e: events.append(e))

    await transitions.classification_started("ABC123")
    assert len(events) == 1
    assert events[0].type == EventType.STORE_CHANGED


async def test_classification_started_reports_admission_decision():
    """分类状态准入由 transition interface 明确返回，拒绝时不重复发事件。"""
    store = FakeStore()
    bus = RecordingBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)
    store.add(_make_item("ADMIT", status=TaskStatus.pending))

    assert await transitions.classification_started("ADMIT") is True
    assert await transitions.classification_started("ADMIT") is False
    assert await transitions.classification_started("MISSING") is False

    changed = [event for event in bus.events if event.type == EventType.STORE_CHANGED]
    assert len(changed) == 1


async def test_classification_started_is_atomic_under_concurrency():
    store = CoordinatedCASStore()
    bus = RecordingBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)
    store.add(_make_item("RACE", status=TaskStatus.pending))

    decisions = await asyncio.gather(
        transitions.classification_started("RACE"),
        transitions.classification_started("RACE"),
    )

    assert sorted(decisions) == [False, True]
    assert store.cas_calls == 2
    assert store.operations[:2] == ["cas", "cas"]
    changed = [event for event in bus.events if event.type == EventType.STORE_CHANGED]
    assert len(changed) == 1


async def test_download_submitting_is_atomic_under_concurrency():
    store = CoordinatedCASStore()
    bus = RecordingBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)
    store.add(_make_item("DOWNLOAD-RACE", status=TaskStatus.pending))

    decisions = await asyncio.gather(
        transitions.download_submitting("DOWNLOAD-RACE"),
        transitions.download_submitting("DOWNLOAD-RACE"),
    )

    admitted = [decision for decision in decisions if decision is not None]
    assert len(admitted) == 1
    assert admitted[0].status == TaskStatus.adding
    assert decisions.count(None) == 1
    assert store.cas_calls == 2
    assert store.operations[:2] == ["cas", "cas"]
    current = store.get("DOWNLOAD-RACE")
    assert current is not None
    assert current.status == TaskStatus.adding
    starts = [event for event in bus.events if event.type == EventType.DOWNLOAD_START]
    assert len(starts) == 1


# ── 2. DOWNLOAD_RESULT: terminal always emits ──


async def test_terminal_status_always_emits_download_result():
    """Terminal statuses (success/error) always emit DOWNLOAD_RESULT via download_state_changed."""
    for terminal_status in (TaskStatus.success, TaskStatus.error):
        store = FakeStore()
        bus = MessageBus()
        transitions = MagnetItemTransitions(store=store, bus=bus)

        item = _make_item("TERM", "terminal", terminal_status)
        store.add(item)

        events = []
        bus.subscribe(EventType.DOWNLOAD_RESULT, lambda e: events.append(e))

        await transitions.download_state_changed("TERM", previous_status=TaskStatus.downloading)
        assert len(events) == 1, f"terminal {terminal_status} should emit"


# ── 3. DOWNLOAD_RESULT: new-phase emits ──


async def test_new_phase_emits_download_result():
    """Transitions from pending/adding/classifying/None emit DOWNLOAD_RESULT."""
    for prev in (TaskStatus.pending, TaskStatus.adding, TaskStatus.classifying, None):
        store = FakeStore()
        bus = MessageBus()
        transitions = MagnetItemTransitions(store=store, bus=bus)

        item = _make_item("NEW", "new phase", TaskStatus.queued)
        store.add(item)

        events = []
        bus.subscribe(EventType.DOWNLOAD_RESULT, lambda e: events.append(e))

        await transitions.download_state_changed("NEW", previous_status=prev)
        assert len(events) == 1, f"new phase from {prev} should emit"


# ── 4. DOWNLOAD_RESULT: routine oscillation does NOT emit ──


async def test_routine_oscillation_suppressed():
    """queued→downloading and back should NOT emit DOWNLOAD_RESULT (noise suppression)."""
    store = FakeStore()
    bus = MessageBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    item = _make_item("OSC", "oscillating", TaskStatus.downloading)
    store.add(item)

    events = []
    bus.subscribe(EventType.DOWNLOAD_RESULT, lambda e: events.append(e))

    await transitions.download_state_changed("OSC", previous_status=TaskStatus.queued)
    assert len(events) == 0, "queued→downloading should be suppressed"

    item2 = MagnetItem(
        hash="OSC2",
        name="osc back",
        magnet="magnet:?xt=urn:btih:OSC2",
        status=TaskStatus.queued,
    )
    store.add(item2)
    events.clear()
    await transitions.download_state_changed("OSC2", previous_status=TaskStatus.downloading)
    assert len(events) == 0, "downloading→queued should be suppressed"


async def test_stale_completion_callbacks_do_not_overwrite_newer_state():
    """过期的分类/提交回调不能覆盖已推进的条目状态。"""
    store = FakeStore()
    bus = RecordingBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    submitted = _make_item("SUBMITTED", status=TaskStatus.error)
    store.add(submitted)
    await transitions.download_submitted("SUBMITTED")
    assert store.get("SUBMITTED").status == TaskStatus.error
    assert bus.events == []

    classified = _make_item("CLASSIFIED", status=TaskStatus.pending)
    store.add(classified)
    await transitions.classified("CLASSIFIED", {"category": "电影", "save_path": "/movies"})
    await transitions.classification_failed("CLASSIFIED", "stale callback")
    current = store.get("CLASSIFIED")
    assert current.status == TaskStatus.pending
    assert current.category is None
    assert current.error_msg is None
    assert bus.events == []


async def test_completion_callbacks_accept_their_expected_source_state():
    """adding/classifying 的正常完成路径保持不变。"""
    store = FakeStore()
    bus = RecordingBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    submitted = _make_item("ADDING", status=TaskStatus.adding)
    store.add(submitted)
    await transitions.download_submitted("ADDING")
    assert store.get("ADDING").status == TaskStatus.queued

    classifying = _make_item("CLASSIFYING", status=TaskStatus.classifying)
    store.add(classifying)
    await transitions.classified("CLASSIFYING", {"category": "电影", "save_path": "/movies"})
    current = store.get("CLASSIFYING")
    assert current.status == TaskStatus.pending
    assert current.category == "电影"


async def test_reconcile_reports_false_when_stale_status_rejects_update():
    store = FakeStore()
    bus = RecordingBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)
    current = _make_item("STALE-SYNC", status=TaskStatus.success)
    store.add(current)
    stale_snapshot = current.model_copy(update={"status": TaskStatus.queued})

    changed = await transitions.reconcile_download_snapshot(
        "STALE-SYNC",
        stale_snapshot,
        {"state": "downloading", "progress": 0.5},
    )

    assert changed is False
    assert store.get("STALE-SYNC").status == TaskStatus.success
    assert bus.events == []


if __name__ == "__main__":
    asyncio.run(test_emit_item_changed_always_emits())
    asyncio.run(test_terminal_status_always_emits_download_result())
    asyncio.run(test_new_phase_emits_download_result())
    asyncio.run(test_routine_oscillation_suppressed())
    print("=== MagnetItemTransitions event emission tests passed! ===")
