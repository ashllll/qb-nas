"""Incremental qBittorrent sync state.

Internal helper that tracks the maindata `rid`, the current torrent snapshot,
and recently removed torrent hashes.  Kept separate from `QBittorrentClient` so
snapshot/removed logic can be unit tested without network I/O.

B8（2026-11-01 真机缺陷）：``/sync/maindata?rid=N`` 的增量 ``torrents`` 条目只带
**变化字段**（progress/dlspeed/eta…），不带 ``state``。旧实现用
``snapshot[hash] = info`` 整体替换，缓存里的 state 被冲成空，mapper 于是判
「qB 种子状态无法识别: 空」；用户一在 qB 里恢复下载，资源库就整片变红。

所以这里守两条：

1. **增量是补丁，不是整条**：按 hash 合并（``setdefault(...).update(info)``），
   ``state`` 等没变化的字段保留上一轮的值；
2. **全量是权威**：``full_update`` / rid 失效 / 我们主动取 rid=0 时，用全量整体
   替换快照——不在全量里的种子已经不存在，不能靠合并留下幽灵。
   首次见到的 hash 在增量里只有变化字段、没有 state 时，补取一次全量把 state 拿回来；
   补过仍缺的记下来，避免每轮都打一次全量。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Dict, Tuple

log = logging.getLogger(__name__)

FetchMaindata = Callable[[int], Awaitable[dict]]

# 兼容接口 take_recently_removed() 的缓存上限：主路径用 poll() 的返回值，
# 这里只留最近有限条，避免常驻服务里长期累积（每天删种子也不会无限长）。
_MAX_RECENTLY_REMOVED = 4096


class QBitSyncState:
    """Holds incremental sync state for the qBittorrent `/sync/maindata` endpoint."""

    def __init__(self) -> None:
        self._maindata_rid = 0
        self._torrent_snapshot: Dict[str, dict] = {}
        self._recently_removed: set[str] = set()
        # 已经为「缺 state」补过一次全量、但仍没拿到 state 的 hash：
        # 记下来避免每轮轮询都再打一次全量（qB 异常时不会变成请求风暴）。
        self._state_pending: set[str] = set()

    async def poll(self, fetch: FetchMaindata) -> Tuple[Dict[str, dict], set[str]]:
        """Fetch the next delta, update the snapshot, and return it atomically.

        Returns ``(snapshot_copy, removed_hashes)`` where *removed_hashes* is the
        set of torrent hashes that were removed in **this** poll cycle.  Callers
        that only need the snapshot can ignore the second element.
        """
        data = await fetch(self._maindata_rid)
        if not data:
            return self._snapshot_copy(), set()

        # qB 在首轮（rid=0）与 rid 失效时用 full_update 标记回全量；带这个标记的响应
        # 就是权威快照，整体替换；其余一律按补丁合并。
        self._apply(data, full_replace=bool(data.get("full_update")))
        removed = self._collect_removed(data)

        missing = self._missing_state_keys()
        if missing - self._state_pending:
            # 增量里首次出现的种子只有变化字段：取一次全量（rid=0）把 state 补回来。
            # 只在**新**的缺 state hash 出现时补，且补完记下仍缺的，防请求风暴。
            full = await fetch(0)
            if full:
                self._apply(full, full_replace=bool(full.get("full_update", True)))
                removed |= self._collect_removed(full)
                log.info(
                    "qB 增量缺 state，已补取全量：补前缺 %d 条，补后缺 %d 条",
                    len(missing),
                    len(self._missing_state_keys()),
                )
            self._state_pending = self._missing_state_keys()
        else:
            self._state_pending &= missing

        return self._snapshot_copy(), removed

    # ── 内部 ──────────────────────────────

    def _apply(self, data: dict, *, full_replace: bool) -> None:
        """把一轮 maindata 应用到快照：全量替换、增量合并。"""
        torrents = data.get("torrents") or {}
        if not isinstance(torrents, dict):
            torrents = {}

        incoming: dict[str, dict] = {}
        for hash_key, info in torrents.items():
            if not isinstance(info, dict):
                # 异常 payload（null/字符串）不能把缓存写成垃圾，也不能让整轮同步炸掉
                log.warning(
                    "qB 同步里 torrent 条目不是对象，跳过 key=%s type=%s",
                    str(hash_key)[:16],
                    type(info).__name__,
                )
                continue
            incoming[str(hash_key).lower()] = info

        if full_replace:
            # 全量是权威快照：qB 重启/rid 失效后，不在全量里的种子已经不存在了
            self._torrent_snapshot = {key: dict(info) for key, info in incoming.items()}
        else:
            # B8 的核心：增量是补丁，不是整条种子
            for key, info in incoming.items():
                self._torrent_snapshot.setdefault(key, {}).update(info)

        self._maindata_rid = data.get("rid", self._maindata_rid)

    def _collect_removed(self, data: dict) -> set[str]:
        removed = {str(h).lower() for h in (data.get("torrents_removed") or [])}
        if not removed:
            return set()
        self._recently_removed |= removed
        if len(self._recently_removed) > _MAX_RECENTLY_REMOVED:
            # 集合无序，截断保留其中一部分即可：这个集合只服务兼容接口，
            # 本轮判定用的是返回的 removed 集合。
            self._recently_removed = set(list(self._recently_removed)[:_MAX_RECENTLY_REMOVED])
        for hash_key in removed:
            self._torrent_snapshot.pop(hash_key, None)
        self._state_pending -= removed
        return removed

    def _missing_state_keys(self) -> set[str]:
        return {
            key
            for key, info in self._torrent_snapshot.items()
            if not str(info.get("state") or "").strip()
        }

    def _snapshot_copy(self) -> Dict[str, dict]:
        """返回快照副本：条目也复制一份，调用方改它不会污染缓存。"""
        return {key: dict(info) for key, info in self._torrent_snapshot.items()}

    def take_recently_removed(self) -> set[str]:
        """Return the set of recently removed hashes and clear it.

        Kept for backward compatibility.  Prefer the second element of
        :meth:`poll` for race-free access.
        """
        removed = set(self._recently_removed)
        self._recently_removed.clear()
        return removed
