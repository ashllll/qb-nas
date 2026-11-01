"""Tests for the internal qBittorrent incremental sync state helper."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.qbit_client.sync_state import QBitSyncState


@pytest.mark.asyncio
async def test_poll_updates_snapshot_and_rid():
    state = QBitSyncState()

    async def fetch(rid: int) -> dict:
        return {
            "rid": rid + 1,
            "torrents": {"ABC": {"state": "downloading", "progress": 0.5}},
        }

    snapshot, removed = await state.poll(fetch)

    assert snapshot == {"abc": {"state": "downloading", "progress": 0.5}}
    assert removed == set()
    assert state._maindata_rid == 1


@pytest.mark.asyncio
async def test_poll_lowercases_hashes():
    state = QBitSyncState()

    async def fetch(_rid: int) -> dict:
        return {
            "rid": 7,
            "torrents": {"MiXeD": {"state": "uploading", "progress": 1.0}},
        }

    snapshot, _removed = await state.poll(fetch)

    assert "mixed" in snapshot
    assert "MiXeD" not in snapshot


@pytest.mark.asyncio
async def test_poll_returns_cached_snapshot_when_fetch_fails():
    state = QBitSyncState()
    state._torrent_snapshot = {"keep": {"state": "pausedUP", "progress": 1.0}}
    state._maindata_rid = 5

    async def fetch(_rid: int) -> dict:
        return {}

    snapshot, removed = await state.poll(fetch)

    assert snapshot == {"keep": {"state": "pausedUP", "progress": 1.0}}
    assert removed == set()
    assert state._maindata_rid == 5


@pytest.mark.asyncio
async def test_poll_tracks_removed_torrents_and_drops_them_from_snapshot():
    state = QBitSyncState()
    state._torrent_snapshot = {
        "gone": {"state": "downloading", "progress": 0.1},
        "stay": {"state": "uploading", "progress": 1.0},
    }

    async def fetch(_rid: int) -> dict:
        return {
            "rid": 2,
            "torrents_removed": ["GONE"],
        }

    snapshot, removed = await state.poll(fetch)

    assert "gone" not in snapshot
    assert "stay" in snapshot
    assert removed == {"gone"}
    assert state.take_recently_removed() == {"gone"}


@pytest.mark.asyncio
async def test_poll_preserves_recently_removed_when_no_new_removals():
    """Accumulated removals must not be lost when a poll returns zero new removals."""
    state = QBitSyncState()
    state._recently_removed = {"old"}

    async def fetch(_rid: int) -> dict:
        return {"rid": 3, "torrents": {"new": {"state": "metaDL", "progress": 0.0}}}

    await state.poll(fetch)

    # Old removals are preserved — only take_recently_removed() clears them.
    assert state.take_recently_removed() == {"old"}


def test_take_recently_removed_returns_and_clears():
    state = QBitSyncState()
    state._recently_removed = {"a", "b"}

    first = state.take_recently_removed()
    second = state.take_recently_removed()

    assert first == {"a", "b"}
    assert second == set()


@pytest.mark.asyncio
async def test_poll_passes_current_rid_to_fetch():
    state = QBitSyncState()
    state._maindata_rid = 42
    seen: list[int] = []

    async def fetch(rid: int) -> dict:
        seen.append(rid)
        return {"rid": rid + 1}

    await state.poll(fetch)

    assert seen == [42]


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_poll_updates_snapshot_and_rid())
    asyncio.run(test_poll_lowercases_hashes())
    asyncio.run(test_poll_returns_cached_snapshot_when_fetch_fails())
    asyncio.run(test_poll_tracks_removed_torrents_and_drops_them_from_snapshot())
    test_take_recently_removed_returns_and_clears()
    asyncio.run(test_poll_passes_current_rid_to_fetch())
    print("=== qB sync state tests passed! ===")


@pytest.mark.asyncio
async def test_recently_removed_cache_is_bounded():
    """take_recently_removed 只是兼容缓存；长期运行不能无限累积。"""
    state = QBitSyncState()

    async def fetch(rid):
        return {"rid": rid + 1, "torrents_removed": [f"{i:040x}" for i in range(500)]}

    for _ in range(20):
        await state.poll(fetch)

    assert len(state._recently_removed) <= 4096


# ── B8：增量是补丁，不是整条种子 ─────────────────────────


def _full(rid: int, torrents: dict, *, flag: bool = True) -> dict:
    return {"rid": rid, "full_update": flag, "torrents": torrents}


def _delta(rid: int, torrents: dict, removed: list | None = None) -> dict:
    return {"rid": rid, "torrents": torrents, "torrents_removed": removed or []}


@pytest.mark.asyncio
async def test_incremental_delta_without_state_keeps_previous_state():
    """B8 核心：增量只带 progress/dlspeed 时，缓存里的 state 不能被冲成空。"""
    state = QBitSyncState()
    responses = [
        _full(1, {"ABC": {"state": "downloading", "progress": 0.1}}),
        _delta(2, {"ABC": {"progress": 0.4, "dlspeed": 1024}}),
    ]

    async def fetch(_rid: int) -> dict:
        return responses.pop(0)

    first, _ = await state.poll(fetch)
    assert first["abc"]["state"] == "downloading"

    second, _ = await state.poll(fetch)
    assert second["abc"]["state"] == "downloading", "增量不能把没变的 state 冲掉"
    assert second["abc"]["progress"] == 0.4
    assert second["abc"]["dlspeed"] == 1024


@pytest.mark.asyncio
async def test_first_seen_hash_without_state_triggers_one_full_refetch():
    """首次见到的 hash 在增量里只有变化字段：补取一次全量把 state 拿回来。"""
    state = QBitSyncState()
    calls: list[int] = []
    responses = [
        _full(1, {"AAA": {"state": "downloading", "progress": 0.1}}),
        _delta(2, {"BBB": {"progress": 0.2}}),
        _full(3, {
            "AAA": {"state": "downloading", "progress": 0.2},
            "BBB": {"state": "metaDL", "progress": 0.2},
        }),
    ]

    async def fetch(rid: int) -> dict:
        calls.append(rid)
        return responses.pop(0)

    await state.poll(fetch)  # 全量
    snapshot, _ = await state.poll(fetch)  # 增量带来新 hash（无 state）→ 补全量

    assert calls == [0, 1, 0], f"应只补一次全量（rid=0），实际请求 rid={calls}"
    assert snapshot["bbb"]["state"] == "metaDL", "补全量后新种子要有 state，不能永久『无法识别』"
    assert state._maindata_rid == 3


@pytest.mark.asyncio
async def test_full_refetch_is_not_repeated_every_round_for_still_missing_state():
    """全量补过仍缺 state 的 hash 记下来，不要每轮都打一次全量（防请求风暴）。"""
    state = QBitSyncState()
    calls: list[int] = []
    responses = [
        _full(1, {}),
        _delta(2, {"BBB": {"progress": 0.2}}),
        _full(3, {"BBB": {"progress": 0.2}}),  # 异常：全量里也没有 state
        _delta(4, {"BBB": {"progress": 0.3}}),
    ]

    async def fetch(rid: int) -> dict:
        calls.append(rid)
        return responses.pop(0)

    await state.poll(fetch)  # rid=0 全量
    await state.poll(fetch)  # rid=1 增量 → 新 hash 缺 state → 补 rid=0
    await state.poll(fetch)  # rid=3 增量 → 仍缺，但已补过，不再补

    assert calls == [0, 1, 0, 3], f"缺 state 不应每轮都补全量，实际请求 rid={calls}"


@pytest.mark.asyncio
async def test_non_dict_torrent_entry_is_skipped_not_crashing():
    state = QBitSyncState()

    async def fetch(_rid: int) -> dict:
        return _delta(5, {"AAA": None, "BBB": "garbage", "CCC": {"state": "downloading"}})

    snapshot, _ = await state.poll(fetch)
    assert snapshot == {"ccc": {"state": "downloading"}}


@pytest.mark.asyncio
async def test_full_update_replaces_stale_snapshot_after_qb_restart():
    """qB 重启/rid 失效会回全量：不在全量里的种子已经不存在，不能留幽灵。"""
    state = QBitSyncState()
    responses = [
        _full(1, {"AAA": {"state": "downloading"}, "OLD": {"state": "downloading"}}),
        _full(2, {"BBB": {"state": "downloading"}}),
    ]

    async def fetch(_rid: int) -> dict:
        return responses.pop(0)

    await state.poll(fetch)
    snapshot, _ = await state.poll(fetch)

    assert set(snapshot) == {"bbb"}, f"全量后旧种子不应残留：{sorted(snapshot)}"


@pytest.mark.asyncio
async def test_returned_snapshot_is_a_copy_not_the_cache():
    """调用方拿到的是副本：改它不能污染缓存（下一轮无新数据时仍应是原值）。"""
    state = QBitSyncState()
    responses: list[dict] = [_full(1, {"AAA": {"state": "downloading"}})]

    async def fetch(_rid: int) -> dict:
        return responses.pop(0) if responses else {}

    snapshot, _ = await state.poll(fetch)
    snapshot["aaa"]["state"] = "error"
    snapshot["zzz"] = {"state": "error"}

    snapshot2, _ = await state.poll(fetch)  # 无新数据 → 直接返回缓存副本
    assert snapshot2["aaa"]["state"] == "downloading"
    assert "zzz" not in snapshot2


@pytest.mark.asyncio
async def test_removed_hash_in_delta_is_dropped_and_does_not_trigger_refetch():
    state = QBitSyncState()
    calls: list[int] = []
    responses = [
        _full(1, {"AAA": {"state": "downloading"}, "BBB": {"state": "downloading"}}),
        _delta(2, {}, removed=["bbb"]),
    ]

    async def fetch(rid: int) -> dict:
        calls.append(rid)
        return responses.pop(0)

    await state.poll(fetch)
    snapshot, removed = await state.poll(fetch)

    assert removed == {"bbb"}
    assert set(snapshot) == {"aaa"}
    assert calls == [0, 1], f"删除事件不应触发全量补取，实际 rid={calls}"
