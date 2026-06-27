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

    snapshot = await state.poll(fetch)

    assert snapshot == {"abc": {"state": "downloading", "progress": 0.5}}
    assert state._maindata_rid == 1


@pytest.mark.asyncio
async def test_poll_lowercases_hashes():
    state = QBitSyncState()

    async def fetch(_rid: int) -> dict:
        return {
            "rid": 7,
            "torrents": {"MiXeD": {"state": "uploading", "progress": 1.0}},
        }

    snapshot = await state.poll(fetch)

    assert "mixed" in snapshot
    assert "MiXeD" not in snapshot


@pytest.mark.asyncio
async def test_poll_returns_cached_snapshot_when_fetch_fails():
    state = QBitSyncState()
    state._torrent_snapshot = {"keep": {"state": "pausedUP", "progress": 1.0}}
    state._maindata_rid = 5

    async def fetch(_rid: int) -> dict:
        return {}

    snapshot = await state.poll(fetch)

    assert snapshot == {"keep": {"state": "pausedUP", "progress": 1.0}}
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

    snapshot = await state.poll(fetch)

    assert "gone" not in snapshot
    assert "stay" in snapshot
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
    asyncio.run(test_poll_clears_recently_removed_when_no_removals())
    test_take_recently_removed_returns_and_clears()
    asyncio.run(test_poll_passes_current_rid_to_fetch())
    print("=== qB sync state tests passed! ===")
