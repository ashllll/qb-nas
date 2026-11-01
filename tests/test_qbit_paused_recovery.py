"""N18 回归：库里被误判成 error 的「已暂停」种子，下一次同步要能自己恢复成 paused。

用户真机上 58 条种子在 qB 里是 stoppedDL，旧映射把它们全写成 status=error +
error_msg="qB 种子状态无法识别: stoppedDL"。只改映射表不够，还要证明：

1. ``paused`` 在同步扫描的活跃状态里（否则恢复后就不再跟踪，用户重新开始下载也看不到变化）；
2. 完整走一遍「读库 → 对快照 → 写回」，58 条从 error 变成 paused，error_msg 清空；
3. 恢复成 paused 之后，用户在 qB 里继续下载 → 下一轮同步能变成 downloading。
"""

from __future__ import annotations


import pytest

from magnet_harvester.bus import MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.services.qbit_sync import _SYNC_ACTIVE_STATUSES, QBitSyncLoop
from magnet_harvester.store import AsyncItemStore, SQLiteItemStore
from magnet_harvester.transitions import DownloadTransitions

LEGACY_ERROR_MSG = "qB 种子状态无法识别: stoppedDL"


class FakeQbit:
    def __init__(self, snapshot: dict[str, dict]):
        self._snapshot = snapshot

    async def poll_torrent_snapshot_with_removed(self):
        return dict(self._snapshot), set()

    def take_recently_removed(self):
        return set()


def _hash(index: int) -> str:
    return f"{index:040X}"


def _seed_error_items(store: SQLiteItemStore, count: int) -> list[str]:
    hashes = []
    for index in range(count):
        hash_key = _hash(index)
        hashes.append(hash_key)
        store.add(
            MagnetItem(
                hash=hash_key,
                name=f"item-{index}",
                magnet=f"magnet:?xt=urn:btih:{hash_key}",
                status=TaskStatus.error,
                torrent_state="stoppedDL",
                error_msg=LEGACY_ERROR_MSG,
            )
        )
    return hashes


def test_paused_is_tracked_by_sync():
    """paused 必须在同步活跃列表里，否则恢复后状态就冻住了。"""
    assert TaskStatus.paused.value in _SYNC_ACTIVE_STATUSES


@pytest.mark.asyncio
async def test_legacy_error_items_recover_to_paused(tmp_path):
    backend = SQLiteItemStore(tmp_path / "items.db")
    store = AsyncItemStore(backend)
    hashes = _seed_error_items(backend, 58)
    snapshot = {
        hash_key.lower(): {"state": "stoppedDL", "progress": 0.0}
        for hash_key in hashes
    }

    bus = MessageBus()
    downloads = DownloadTransitions(store=store, bus=bus)
    loop = QBitSyncLoop(
        qbit_client=FakeQbit(snapshot),
        store=store,
        bus=bus,
        downloads=downloads,
    )

    tracked = await store.list(status=_SYNC_ACTIVE_STATUSES, limit=50000)
    assert len(tracked) == 58, "error 条目必须在同步扫描范围内，否则永远改不回来"

    await loop._reconcile_batch(tracked, snapshot, set())

    recovered = await store.list(status=[TaskStatus.paused.value], limit=100)
    assert len(recovered) == 58
    assert all(item.error_msg is None for item in recovered)
    assert all(item.torrent_state == "stoppedDL" for item in recovered)
    assert await store.count_items(status=TaskStatus.error.value) == 0


@pytest.mark.asyncio
async def test_paused_item_follows_qb_when_download_resumes(tmp_path):
    backend = SQLiteItemStore(tmp_path / "items.db")
    store = AsyncItemStore(backend)
    hash_key = _hash(1)
    backend.add(
        MagnetItem(
            hash=hash_key,
            name="paused-item",
            magnet=f"magnet:?xt=urn:btih:{hash_key}",
            status=TaskStatus.paused,
            torrent_state="stoppedDL",
        )
    )
    snapshot = {hash_key.lower(): {"state": "downloading", "progress": 0.35}}

    bus = MessageBus()
    loop = QBitSyncLoop(
        qbit_client=FakeQbit(snapshot),
        store=store,
        bus=bus,
        downloads=DownloadTransitions(store=store, bus=bus),
    )

    tracked = await store.list(status=_SYNC_ACTIVE_STATUSES, limit=100)
    assert [item.hash for item in tracked] == [hash_key]
    await loop._reconcile_batch(tracked, snapshot, set())

    item = await store.get(hash_key)
    assert item is not None
    assert item.status == TaskStatus.downloading
    assert item.progress == 35.0
