"""Incremental qBittorrent sync state.

Internal helper that tracks the maindata `rid`, the current torrent snapshot,
and recently removed torrent hashes.  Kept separate from `QBittorrentClient` so
snapshot/removed logic can be unit tested without network I/O.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Dict, Tuple


FetchMaindata = Callable[[int], Awaitable[dict]]


class QBitSyncState:
    """Holds incremental sync state for the qBittorrent `/sync/maindata` endpoint."""

    def __init__(self) -> None:
        self._maindata_rid = 0
        self._torrent_snapshot: Dict[str, dict] = {}
        self._recently_removed: set[str] = set()

    async def poll(self, fetch: FetchMaindata) -> Tuple[Dict[str, dict], set[str]]:
        """Fetch the next delta, update the snapshot, and return it atomically.

        Returns ``(snapshot_copy, removed_hashes)`` where *removed_hashes* is the
        set of torrent hashes that were removed in **this** poll cycle.  Callers
        that only need the snapshot can ignore the second element.
        """
        data = await fetch(self._maindata_rid)
        if not data:
            return dict(self._torrent_snapshot), set()

        self._maindata_rid = data.get("rid", self._maindata_rid)

        torrents = data.get("torrents", {}) or {}
        if not isinstance(torrents, dict):
            torrents = {}
        for hash_key, info in torrents.items():
            self._torrent_snapshot[hash_key.lower()] = info

        removed = {str(h).lower() for h in (data.get("torrents_removed") or [])}
        if removed:
            self._recently_removed |= removed
            for hash_key in removed:
                self._torrent_snapshot.pop(hash_key, None)

        return dict(self._torrent_snapshot), removed

    def take_recently_removed(self) -> set[str]:
        """Return the set of recently removed hashes and clear it.

        Kept for backward compatibility.  Prefer the second element of
        :meth:`poll` for race-free access.
        """
        removed = set(self._recently_removed)
        self._recently_removed.clear()
        return removed
