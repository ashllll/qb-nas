"""B8 回归：qB 增量同步必须合并，不能把「变化字段」当整条种子覆盖缓存。

真机现象（2026-11-01）：用户在 qB 里恢复下载后，58 条里 23–45 条显示
「失败：qB 种子状态无法识别: 空」；重启后 t+0..t+60 仍 30–37 条。

根因：`GET /sync/maindata?rid=N` 的增量条目只带变化字段（progress/dlspeed/eta…），
不带 state；旧实现 `snapshot[hash] = info` 整体替换，把缓存里的 state 冲成空，
mapper 于是判失败。全量本身是好的，但活跃下载每秒都在产生增量，所以重启也救不回来。

这里用「58 条 error + 全量带 state + 连续 3 轮不带 state 的增量」把整条链路走一遍：
同步后必须是 0 条 error，状态与 qB 一致。
"""

from __future__ import annotations

import pytest

from magnet_harvester.bus import NullBus
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.qbit_client.sync_state import QBitSyncState
from magnet_harvester.services.qbit_sync import _SYNC_ACTIVE_STATUSES, QBitSyncLoop
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import DownloadTransitions

LEGACY_ERROR_MSG = "qB 种子状态无法识别: 空"


class ScriptedQbit:
    """把一个 maindata 响应序列喂给真实的 QBitSyncState，记录请求过的 rid。"""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.state = QBitSyncState()
        self.rid_calls: list[int] = []

    async def poll_torrent_snapshot_with_removed(self):
        async def fetch(rid: int) -> dict:
            self.rid_calls.append(rid)
            if self._responses:
                return self._responses.pop(0)
            # 脚本放完：保持一个空增量，别让测试因为没数据而假绿/假红
            return {"rid": self.state._maindata_rid, "torrents": {}}

        return await self.state.poll(fetch)

    def take_recently_removed(self):
        return set()


def _hash(index: int) -> str:
    return f"{index:040X}"


def _torrent(state: str, progress: float = 0.3) -> dict:
    return {"state": state, "progress": progress, "downloaded": progress * 100, "total_size": 100}


def _seed_error_items(store: AsyncItemStore, count: int) -> list[str]:
    hashes = []
    for index in range(count):
        hash_key = _hash(index)
        hashes.append(hash_key)
        store._backend.add(
            MagnetItem(
                hash=hash_key,
                name=f"user-item-{index}",
                magnet=f"magnet:?xt=urn:btih:{hash_key}",
                status=TaskStatus.error,
                torrent_state="",
                error_msg=LEGACY_ERROR_MSG,
            )
        )
    return hashes


async def _sync_rounds(store, loop, qbit, rounds: int) -> None:
    for _ in range(rounds):
        snapshot, removed = await qbit.poll_torrent_snapshot_with_removed()
        tracked = await store.list(status=_SYNC_ACTIVE_STATUSES, limit=1000)
        await loop._reconcile_batch(tracked, snapshot, removed)


def _build_loop(store, bus):
    return QBitSyncLoop(
        qbit_client=None,
        store=store,
        bus=bus,
        downloads=DownloadTransitions(store=store, bus=bus),
    )


@pytest.mark.asyncio
async def test_58_error_items_stay_in_sync_through_three_deltas_without_state():
    store = AsyncItemStore(InMemoryItemStore())
    bus = NullBus()
    hashes = _seed_error_items(store, 58)

    # qB 真实分布：25 downloading / 26 queuedDL（映射 queued）/ 7 metaDL（映射 downloading）
    states = ["downloading"] * 25 + ["queuedDL"] * 26 + ["metaDL"] * 7
    full = {
        "rid": 1,
        "full_update": True,
        "torrents": {h.lower(): _torrent(state) for h, state in zip(hashes, states)},
    }
    # 增量只带变化字段：qB 在活跃下载时每轮都这样。
    # 取后半段（含 queuedDL/metaDL 这些 progress=0 的种子）——真机 26/27 条
    # 缺 state 的就是它们：旧实现会把它们判成「无法识别: 空」。
    delta = {
        "rid": 2,
        "torrents": {
            h.lower(): {"progress": 0.0, "dlspeed": 0, "eta": 120} for h in hashes[31:]
        },
    }
    qbit = ScriptedQbit(
        [
            full,
            {**delta, "rid": 2},
            {**delta, "rid": 3},
            {**delta, "rid": 4},
        ]
    )
    loop = _build_loop(store, bus)

    await _sync_rounds(store, loop, qbit, rounds=4)

    error_total = await store.count_items(status=TaskStatus.error.value)
    assert error_total == 0, "增量不带 state 不能把种子打成失败"
    assert qbit.rid_calls == [0, 1, 2, 3], (
        f"整个过程中不应出现额外的全量补取（缓存合并已保留 state）：{qbit.rid_calls}"
    )

    by_status = {}
    for item in await store.list(limit=1000):
        by_status.setdefault(item.status.value, 0)
        by_status[item.status.value] += 1
        assert item.error_msg is None
        # 增量里出现过的 27 条进度变成 0（排队/取元数据），其余保持全量里的 30%
        expected_progress = 0.0 if int(item.hash, 16) >= 31 else 30.0
        assert item.progress == expected_progress, (item.hash, item.progress)
    assert by_status == {"downloading": 32, "queued": 26}, by_status


@pytest.mark.asyncio
async def test_new_hash_first_seen_in_delta_without_state_is_not_error():
    """增量里首次见到的 hash 只有 progress：补一次全量拿 state，别永久『无法识别』。"""
    store = AsyncItemStore(InMemoryItemStore())
    bus = NullBus()
    new_hash = _hash(99)
    store._backend.add(
        MagnetItem(
            hash=new_hash,
            name="just-added",
            magnet=f"magnet:?xt=urn:btih:{new_hash}",
            status=TaskStatus.adding,
        )
    )
    qbit = ScriptedQbit(
        [
            {"rid": 1, "full_update": True, "torrents": {}},
            {"rid": 2, "torrents": {new_hash.lower(): {"progress": 0.2}}},
            {
                "rid": 3,
                "full_update": True,
                "torrents": {new_hash.lower(): _torrent("queuedDL", progress=0.2)},
            },
        ]
    )
    loop = _build_loop(store, bus)

    await _sync_rounds(store, loop, qbit, rounds=2)

    item = await store.get(new_hash)
    assert item is not None
    assert item.status == TaskStatus.queued, item.status
    assert item.error_msg is None
    assert qbit.rid_calls == [0, 1, 0], f"应补一次全量后拿到 state：{qbit.rid_calls}"


@pytest.mark.asyncio
async def test_missing_state_keeps_status_and_only_updates_progress():
    """字段还没到 ≠ 状态未知：状态不动，进度照常更新，绝不写成失败。"""
    store = AsyncItemStore(InMemoryItemStore())
    bus = NullBus()
    hash_key = _hash(7)
    store._backend.add(
        MagnetItem(
            hash=hash_key,
            name="downloading-item",
            magnet=f"magnet:?xt=urn:btih:{hash_key}",
            status=TaskStatus.downloading,
            progress=10.0,
            torrent_state="downloading",
        )
    )
    loop = _build_loop(store, bus)
    item = await store.get(hash_key)
    assert item is not None

    changed = await loop._downloads.reconcile_snapshot(hash_key, item, {"progress": 0.5})

    assert changed is True
    updated = await store.get(hash_key)
    assert updated is not None
    assert updated.status == TaskStatus.downloading
    assert updated.progress == 50.0
    assert updated.torrent_state == "downloading"
    assert updated.error_msg is None


@pytest.mark.asyncio
async def test_removed_torrent_still_marks_item_error():
    """torrents_removed 的语义不能被合并逻辑改掉：删掉的种子仍然要报『已消失』。"""
    store = AsyncItemStore(InMemoryItemStore())
    bus = NullBus()
    hash_key = _hash(8)
    store._backend.add(
        MagnetItem(
            hash=hash_key,
            name="removed-item",
            magnet=f"magnet:?xt=urn:btih:{hash_key}",
            status=TaskStatus.downloading,
        )
    )
    loop = _build_loop(store, bus)
    item = await store.get(hash_key)
    assert item is not None

    await loop._downloads.reconcile_snapshot(hash_key, item, None, was_removed=True)

    updated = await store.get(hash_key)
    assert updated is not None
    assert updated.status == TaskStatus.error
    assert updated.torrent_state == "removed"
