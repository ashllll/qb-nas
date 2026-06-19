"""User-facing runtime status and stats snapshots."""

from __future__ import annotations

from typing import Protocol

from magnet_harvester.models import TaskStatus


class StoreLike(Protocol):
    @property
    def count(self) -> int: ...
    def stats(self): ...


class QBitLike(Protocol):
    async def ping(self) -> bool: ...
    def get_stats(self) -> dict: ...


class StatsLike(Protocol):
    def record_api_call(self) -> None: ...
    def as_dict(self) -> dict: ...


class BroadcasterLike(Protocol):
    @property
    def active_count(self) -> int: ...


class ErrorHandlerLike(Protocol):
    def get_error_stats(self) -> dict: ...


class ObservabilitySnapshot:
    """Builds API-facing runtime snapshots behind one interface."""

    def __init__(
        self,
        *,
        store: StoreLike,
        qbit: QBitLike,
        stats: StatsLike | None = None,
        broadcaster: BroadcasterLike | None = None,
        error_handler: ErrorHandlerLike | None = None,
    ):
        self._store = store
        self._qbit = qbit
        self._stats = stats
        self._broadcaster = broadcaster
        self._error_handler = error_handler

    async def system_status(self) -> dict:
        qbit_ok = await self._qbit.ping()
        by_status = self._store.stats().by_status
        tracked = sum(
            by_status.get(status.value, 0)
            for status in (TaskStatus.adding, TaskStatus.queued, TaskStatus.downloading)
        )
        return {
            "qbittorrent": "online" if qbit_ok else "offline",
            "classifier": "local_rules",
            "items_count": self._store.count,
            "tracked_downloads": tracked,
            "qbit_stats": self._qbit.get_stats(),
            "disk_space": {},
        }

    async def health(self) -> dict:
        qbit_ok = await self._qbit.ping()
        return {"healthy": qbit_ok, "qbittorrent": qbit_ok, "classifier": True}

    def api_stats(self) -> dict:
        if self._stats is not None:
            self._stats.record_api_call()
            result = self._stats.as_dict()
        else:
            result = {"api_calls": 0}
        result["active_items"] = self._store.count
        result["websocket_clients"] = (
            self._broadcaster.active_count if self._broadcaster is not None else 0
        )
        if self._error_handler is not None:
            result["error_stats"] = self._error_handler.get_error_stats()
        return result
