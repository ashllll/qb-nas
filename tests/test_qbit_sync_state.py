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


@pytest.mark.asyncio
async def test_poll_does_not_advance_rid_when_the_delta_is_malformed():
    """解析抛错时 rid 必须停在原地。

    rid 一旦推进，qB 就认为这份 delta 已送达，下一轮不会再重发同一份响应里的
    ``torrents_removed`` —— 那条移除就永久丢了。所以 rid 只能在整个 delta 被
    完整吃下之后才推进。
    """
    state = QBitSyncState()
    state._maindata_rid = 7

    async def fetch(_rid: int) -> dict:
        return {"rid": 9, "torrents_removed": 42}  # 非可迭代 → 解析抛错

    with pytest.raises(TypeError, match="not iterable"):
        await state.poll(fetch)

    assert state._maindata_rid == 7


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
    asyncio.run(test_poll_does_not_advance_rid_when_the_delta_is_malformed())
    asyncio.run(test_poll_passes_current_rid_to_fetch())
    print("=== qB sync state tests passed! ===")
