"""Incremental qBittorrent sync state.

Internal helper that tracks the maindata `rid`, the current torrent snapshot,
and recently removed torrent hashes.  Kept separate from `QBittorrentClient` so
snapshot/removed logic can be unit tested without network I/O.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Dict


FetchMaindata = Callable[[int], Awaitable[dict]]


class QBitSyncState:
    """Holds incremental sync state for the qBittorrent `/sync/maindata` endpoint."""

    def __init__(self) -> None:
        self._maindata_rid = 0
        self._torrent_snapshot: Dict[str, dict] = {}
        self._recently_removed: set[str] = set()

    async def poll(self, fetch: FetchMaindata) -> Dict[str, dict]:
        """Fetch the next delta, update the snapshot, and return a copy of it."""
        data = await fetch(self._maindata_rid)
        if not data:
            return dict(self._torrent_snapshot)

        self._maindata_rid = data.get("rid", self._maindata_rid)

        torrents = data.get("torrents", {}) or {}
        for hash_key, info in torrents.items():
            self._torrent_snapshot[hash_key.lower()] = info

        removed = {str(h).lower() for h in data.get("torrents_removed", [])}
        if removed:
            self._recently_removed |= removed
            for hash_key in removed:
                self._torrent_snapshot.pop(hash_key, None)

        return dict(self._torrent_snapshot)

    def take_recently_removed(self) -> set[str]:
        """Return the set of recently removed hashes and clear it."""
        removed = set(self._recently_removed)
        self._recently_removed.clear()
        return removed
