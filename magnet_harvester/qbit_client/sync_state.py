"""Incremental qBittorrent sync state.

Internal helper that tracks the maindata `rid` and the current torrent snapshot.
Kept separate from `QBittorrentClient` so snapshot/removed logic can be unit
tested without network I/O.
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

    async def poll(self, fetch: FetchMaindata) -> Tuple[Dict[str, dict], set[str]]:
        """Fetch the next delta, update the snapshot, and return it atomically.

        Returns ``(snapshot_copy, removed_hashes)`` where *removed_hashes* is the
        set of torrent hashes that were removed in **this** poll cycle.  qB 的
        ``torrents_removed`` 只在发生的那一轮出现，之后不再重发 —— 消费方负责
        保留它直到真正处理过（见 ``QBitSyncLoop._pending_removed``）。
        """
        data = await fetch(self._maindata_rid)
        if not data:
            return dict(self._torrent_snapshot), set()

        torrents = data.get("torrents", {}) or {}
        if not isinstance(torrents, dict):
            torrents = {}
        for hash_key, info in torrents.items():
            self._torrent_snapshot[hash_key.lower()] = info

        removed = {str(h).lower() for h in (data.get("torrents_removed") or [])}
        if removed:
            for hash_key in removed:
                self._torrent_snapshot.pop(hash_key, None)

        # rid 只在整份 delta 被完整吃下之后才推进：解析中途抛错时 qB 下一轮还会
        # 重发同一份响应（含 torrents_removed），移除不会悄悄过站。
        self._maindata_rid = data.get("rid", self._maindata_rid)

        return dict(self._torrent_snapshot), removed
