"""
Test QBitSyncLoop — background polling service for qBittorrent state sync.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.bus import EventType, MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import FakeStore
from magnet_harvester.services.qbit_sync import QBitSyncLoop
from magnet_harvester.transitions import MagnetItemTransitions


class FakeQbitClient:
    def __init__(self):
        self._snapshot = {}
        self._removed = set()

    async def poll_torrent_snapshot(self):
        return dict(self._snapshot)

    def take_recently_removed(self):
        removed = set(self._removed)
        self._removed.clear()
        return removed

    def map_torrent_status(self, torrent):
        from magnet_harvester.models import TaskStatus
        state = str(torrent.get("state", ""))
        progress = float(torrent.get("progress") or 0.0)
        if state in {"error", "missingFiles"}:
            return {"status": TaskStatus.error, "progress": 0.0, "torrent_state": state}
        if progress >= 1.0:
            return {"status": TaskStatus.success, "progress": 100.0, "torrent_state": state}
        return {"status": TaskStatus.downloading, "progress": round(progress * 100, 1), "torrent_state": state}


class FakeTaskManager:
    def __init__(self):
        self.calls = []

    def create(self, coro, name=None):
        self.calls.append(name)
        return asyncio.create_task(coro, name=name)


@pytest.mark.asyncio
async def test_lifecycle_start_stop():
    qbit = FakeQbitClient()
    store = FakeStore()
    bus = MessageBus()

    loop = QBitSyncLoop(qbit_client=qbit, store=store, bus=bus, poll_interval=0.05)

    await loop.start()
    assert loop._task is not None
    assert not loop._task.done()

    await loop.stop()
    assert loop._task.done()


@pytest.mark.asyncio
async def test_start_uses_injected_task_manager():
    qbit = FakeQbitClient()
    store = FakeStore()
    bus = MessageBus()
    tasks = FakeTaskManager()

    loop = QBitSyncLoop(
        qbit_client=qbit,
        store=store,
        bus=bus,
        poll_interval=0.05,
        task_manager=tasks,
    )

    await loop.start()
    assert loop._task is not None
    assert tasks.calls == ["qbit-sync-loop"]

    await loop.stop()


@pytest.mark.asyncio
async def test_sync_updates_tracked_item_status():
    qbit = FakeQbitClient()
    store = FakeStore()
    bus = MessageBus()

    # Seed a tracked item
    item = MagnetItem(
        hash="AAAA",
        name="Test",
        magnet="magnet:?xt=urn:btih:AAAA",
        status=TaskStatus.queued,
    )
    store.add(item)

    # qB reports it as downloading
    qbit._snapshot = {"aaaa": {"state": "downloading", "progress": 0.42}}

    loop = QBitSyncLoop(qbit_client=qbit, store=store, bus=bus, poll_interval=0.05)
    await loop.start()
    await asyncio.sleep(0.15)  # Let one poll cycle run
    await loop.stop()

    updated = store.get("AAAA")
    assert updated.status == TaskStatus.downloading
    assert updated.progress == 42.0


@pytest.mark.asyncio
async def test_sync_detects_removed_torrent():
    qbit = FakeQbitClient()
    store = FakeStore()
    bus = MessageBus()

    item = MagnetItem(
        hash="BBBB",
        name="Test",
        magnet="magnet:?xt=urn:btih:BBBB",
        status=TaskStatus.downloading,
    )
    store.add(item)

    qbit._removed = {"bbbb"}

    loop = QBitSyncLoop(qbit_client=qbit, store=store, bus=bus, poll_interval=0.05)
    await loop.start()
    await asyncio.sleep(0.15)
    await loop.stop()

    updated = store.get("BBBB")
    assert updated.status == TaskStatus.error
    assert updated.torrent_state == "removed"


@pytest.mark.asyncio
async def test_no_tracked_items_skips_poll():
    qbit = FakeQbitClient()
    store = FakeStore()
    bus = MessageBus()

    loop = QBitSyncLoop(qbit_client=qbit, store=store, bus=bus, poll_interval=0.05)
    await loop.start()
    await asyncio.sleep(0.15)
    await loop.stop()

    # No crash, no changes to empty store
    assert store.count == 0


class FakeTransitions:
    """Records reconcile_download_snapshot calls for delegation assertions."""

    def __init__(self):
        self.calls = []

    async def reconcile_download_snapshot(
        self,
        hash_key: str,
        item: MagnetItem,
        torrent: dict | None,
        mapper,
        *,
        was_removed: bool = False,
    ) -> bool:
        self.calls.append(
            {
                "hash_key": hash_key,
                "item": item,
                "torrent": torrent,
                "was_removed": was_removed,
            }
        )
        return True


class RecordingBus(MessageBus):
    """Records every emitted event for event-order assertions."""

    def __init__(self):
        super().__init__()
        self.events = []

    async def emit(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_sync_delegates_reconciliation_to_transitions():
    qbit = FakeQbitClient()
    store = FakeStore()
    bus = MessageBus()
    transitions = FakeTransitions()

    present_item = MagnetItem(
        hash="PRESENT",
        name="Present",
        magnet="magnet:?xt=urn:btih:PRESENT",
        status=TaskStatus.queued,
    )
    removed_item = MagnetItem(
        hash="REMOVED",
        name="Removed",
        magnet="magnet:?xt=urn:btih:REMOVED",
        status=TaskStatus.downloading,
    )
    store.add(present_item)
    store.add(removed_item)

    qbit._snapshot = {"present": {"state": "downloading", "progress": 0.42}}
    qbit._removed = {"removed"}

    loop = QBitSyncLoop(
        qbit_client=qbit,
        store=store,
        bus=bus,
        poll_interval=0.05,
        transitions=transitions,
    )
    await loop.start()
    await asyncio.sleep(0.15)
    await loop.stop()

    calls_by_hash = {c["hash_key"]: c for c in transitions.calls}
    assert set(calls_by_hash) == {"PRESENT", "REMOVED"}

    present_calls = [c for c in transitions.calls if c["hash_key"] == "PRESENT"]
    assert any(
        c["item"] == present_item
        and c["torrent"] == {"state": "downloading", "progress": 0.42}
        and c["was_removed"] is False
        for c in present_calls
    )

    removed_calls = [c for c in transitions.calls if c["hash_key"] == "REMOVED"]
    assert any(
        c["item"] == removed_item
        and c["torrent"] is None
        and c["was_removed"] is True
        for c in removed_calls
    )


@pytest.mark.asyncio
async def test_sync_routine_queued_to_downloading_does_not_emit_download_result():
    qbit = FakeQbitClient()
    store = FakeStore()
    bus = RecordingBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    item = MagnetItem(
        hash="OSC",
        name="Oscillating",
        magnet="magnet:?xt=urn:btih:OSC",
        status=TaskStatus.queued,
    )
    store.add(item)

    qbit._snapshot = {"osc": {"state": "downloading", "progress": 0.42}}

    loop = QBitSyncLoop(
        qbit_client=qbit,
        store=store,
        bus=bus,
        poll_interval=0.05,
        transitions=transitions,
    )
    await loop.start()
    await asyncio.sleep(0.15)
    await loop.stop()

    updated = store.get("OSC")
    assert updated.status == TaskStatus.downloading
    assert updated.progress == 42.0
    assert any(e.type == EventType.STORE_CHANGED for e in bus.events)
    assert not any(e.type == EventType.DOWNLOAD_RESULT for e in bus.events)


@pytest.mark.asyncio
async def test_sync_removed_torrent_emits_store_changed_and_download_result():
    qbit = FakeQbitClient()
    store = FakeStore()
    bus = RecordingBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    item = MagnetItem(
        hash="GONE",
        name="Gone",
        magnet="magnet:?xt=urn:btih:GONE",
        status=TaskStatus.downloading,
    )
    store.add(item)

    qbit._removed = {"gone"}

    loop = QBitSyncLoop(
        qbit_client=qbit,
        store=store,
        bus=bus,
        poll_interval=0.05,
        transitions=transitions,
    )
    await loop.start()
    await asyncio.sleep(0.15)
    await loop.stop()

    updated = store.get("GONE")
    assert updated.status == TaskStatus.error
    assert updated.torrent_state == "removed"
    assert any(e.type == EventType.STORE_CHANGED for e in bus.events)
    assert any(e.type == EventType.DOWNLOAD_RESULT for e in bus.events)
