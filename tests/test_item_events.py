"""
Test MagnetItemTransitions event emission rules (ItemEventEmitter inlined).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio

from magnet_harvester.bus import EventType, MessageBus
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import FakeStore


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
        hash="OSC2", name="osc back",
        magnet="magnet:?xt=urn:btih:OSC2",
        status=TaskStatus.queued,
    )
    store.add(item2)
    events.clear()
    await transitions.download_state_changed("OSC2", previous_status=TaskStatus.downloading)
    assert len(events) == 0, "downloading→queued should be suppressed"


if __name__ == "__main__":
    asyncio.run(test_emit_item_changed_always_emits())
    asyncio.run(test_terminal_status_always_emits_download_result())
    asyncio.run(test_new_phase_emits_download_result())
    asyncio.run(test_routine_oscillation_suppressed())
    print("=== MagnetItemTransitions event emission tests passed! ===")
