"""Integration tests: QBitSyncLoop reconciliation with item states."""

from __future__ import annotations

import asyncio

from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.bus import NullBus
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.transitions import MagnetItemTransitions


class _SnapshotQBit:
    """Fake qB client that returns a fixed torrent snapshot."""

    def __init__(self, snapshot: dict | None = None, removed: set[str] | None = None):
        self._snapshot = snapshot or {}
        self._removed = removed or set()

    async def poll_torrent_snapshot(self) -> dict:
        return self._snapshot

    async def poll_torrent_snapshot_with_removed(self) -> tuple[dict, set[str]]:
        removed = set(self._removed)
        self._removed = set()
        return self._snapshot, removed

    def take_recently_removed(self) -> set[str]:
        h = self._removed
        self._removed = set()
        return h


def _reconcile(store, transitions, snapshot, removed_hashes):
    """Drive a single reconciliation pass over tracked items."""
    tracked = [
        it for it in store.list(limit=store.count)
        if it.status in {
            TaskStatus.adding, TaskStatus.queued,
            TaskStatus.downloading, TaskStatus.error,
        }
    ]
    for t_item in tracked:
        hash_key = t_item.hash.lower()
        torrent = snapshot.get(hash_key)
        was_removed = hash_key in removed_hashes
        # We can't await inside a sync helper without an event loop,
        # so run reconciliation per-item
        yield t_item, torrent, was_removed


def test_qbit_sync_marks_downloaded_item_as_success():
    """When qB reports a torrent as 'completed', the item should be marked success."""
    store = InMemoryItemStore()
    bus = NullBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    store.add(MagnetItem(
        hash="SYNCTEST001",
        name="Sync.Test",
        magnet="magnet:?xt=urn:btih:SYNCTEST001",
        category="电影",
        status=TaskStatus.downloading,
        progress=0.5,
        torrent_state="downloading",
    ))

    snapshot = {
        "synctest001": {
            "hash": "synctest001", "name": "Sync.Test",
            "progress": 1.0, "state": "completed", "amount_left": 0,
        }
    }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            transitions.reconcile_download_snapshot(
                "SYNCTEST001",
                store.get("SYNCTEST001"),
                snapshot.get("synctest001"),
            )
        )
    finally:
        loop.close()

    updated = store.get("SYNCTEST001")
    assert updated is not None
    assert updated.status == TaskStatus.success


def test_qbit_sync_marks_removed_torrent_as_error():
    """When a tracked torrent disappears from qB, it should be marked as error/removed."""
    store = InMemoryItemStore()
    bus = NullBus()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    store.add(MagnetItem(
        hash="REMOVED001",
        name="Removed.Torrent",
        magnet="magnet:?xt=urn:btih:REMOVED001",
        category="电视剧",
        status=TaskStatus.downloading,
        progress=0.3,
        torrent_state="downloading",
    ))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            transitions.reconcile_download_snapshot(
                "REMOVED001",
                store.get("REMOVED001"),
                None,
                was_removed=True,
            )
        )
    finally:
        loop.close()

    updated = store.get("REMOVED001")
    assert updated is not None
    assert updated.status == TaskStatus.error
    assert updated.error_msg is not None
