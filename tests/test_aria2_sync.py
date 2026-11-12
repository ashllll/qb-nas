from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.bus import EventType
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.services.qbit_sync import QBitSyncLoop
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from tests.test_transitions_events import RecordingBus


@pytest.mark.parametrize(
    ("status", "progress", "error"),
    [
        (TaskStatus.downloading, 25.0, None),
        (TaskStatus.paused, 25.0, None),
        (TaskStatus.success, 100.0, None),
        (TaskStatus.error, 25.0, "下载中心磁盘不可写"),
    ],
)
async def test_bridge_snapshot_updates_store_and_versioned_events(status, progress, error):
    store = AsyncItemStore(InMemoryItemStore())
    bus = RecordingBus()
    hash_key = "a" * 40
    await store.add(
        MagnetItem(
            hash=hash_key,
            name="test",
            magnet=f"magnet:?xt=urn:btih:{hash_key}",
            status=TaskStatus.queued,
        )
    )

    class Bridge:
        async def poll_torrent_snapshot_with_removed(self):
            return {
                hash_key: {
                    "download_state": {
                        "status": status,
                        "progress": progress,
                        "torrent_state": "aria2:test",
                        "error_msg": error,
                    }
                }
            }, set()

    bridge = Bridge()
    loop = QBitSyncLoop(bridge, store, bus)
    await loop._sync_round(bridge, store)
    item = await store.get(hash_key)
    assert (item.status, item.progress, item.error_msg) == (status, progress, error)
    event = next(e for e in bus.events if e.type == EventType.STORE_CHANGED)
    assert event.data["item"]["updated_at"] == item.updated_at


async def test_pipeline_allows_bridge_recovery_window(monkeypatch):
    from magnet_harvester.pipeline import HarvestPipeline
    from tests.test_pipeline_phases import FakeCrawlPhase, FakeClassifyPhase, FakeDownloadPhase

    store = AsyncItemStore(InMemoryItemStore())
    bus = RecordingBus()
    bridge = FakeDownloadPhase()
    bridge.submission_timeout = 120.0
    hash_key = "b" * 40
    await store.add(
        MagnetItem(
            hash=hash_key,
            name="test",
            magnet=f"magnet:?xt=urn:btih:{hash_key}",
            category="电影",
            status=TaskStatus.pending,
        )
    )
    timeouts: list[float] = []
    wait_for = asyncio.wait_for

    async def record_timeout(awaitable, timeout):
        timeouts.append(timeout)
        return await wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", record_timeout)
    pipeline = HarvestPipeline(FakeCrawlPhase(), FakeClassifyPhase(), bridge, store, bus)
    await pipeline.download([hash_key])
    assert timeouts[0] >= bridge.submission_timeout
    assert (await store.get(hash_key)).status == TaskStatus.queued
