"""
Test QBitSyncLoop — background polling service for qBittorrent state sync.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import FakeStore
from magnet_harvester.bus import MessageBus, Event, EventType
from magnet_harvester.services.qbit_sync import QBitSyncLoop


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
