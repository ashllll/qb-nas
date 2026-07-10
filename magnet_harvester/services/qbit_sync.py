"""
QBitSyncLoop — background polling service for qBittorrent state sync.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.models import TaskStatus
from magnet_harvester.store import ItemStore, call_store
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)

_MAX_STORE_ITEMS = 50000

# 非终态 — qBittorrent 同步只关心已提交到 qB 的活跃条目
_SYNC_ACTIVE_STATUSES = [
    TaskStatus.adding.value,
    TaskStatus.queued.value,
    TaskStatus.downloading.value,
    TaskStatus.error.value,
]


class QBitSyncClient(Protocol):
    async def poll_torrent_snapshot(self) -> dict: ...
    async def poll_torrent_snapshot_with_removed(self) -> tuple[dict, set[str]]: ...
    def take_recently_removed(self) -> set[str]: ...


@dataclass
class SyncBackoffPolicy:
    """Tracks qB sync retry delay after consecutive poll failures."""

    base_delay: float
    max_delay: float
    failures: int = 0

    def next_delay(self) -> float:
        if self.failures <= 0:
            return self.base_delay
        return min(self.max_delay, self.base_delay * (2**self.failures))

    def record_success(self) -> None:
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1


@dataclass
class _ReconcileProgress:
    processed: int = 0


class QBitSyncLoop:
    """Polls qBittorrent state and reconciles tracked items."""

    def __init__(
        self,
        qbit_client: QBitSyncClient,
        store: ItemStore,
        bus: MessageBus,
        poll_interval: float = 2.0,
        max_failure_backoff: float = 30.0,
        task_manager: BackgroundTaskSpawner | None = None,
        transitions: MagnetItemTransitions | None = None,
    ):
        self._qbit = qbit_client
        self._store = store
        self._bus = bus
        self._backoff = SyncBackoffPolicy(
            base_delay=poll_interval,
            max_delay=max_failure_backoff,
        )
        self._task_manager = task_manager
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._transitions = transitions or MagnetItemTransitions(store=store, bus=bus)

    async def start(self):
        async with self._lock:
            if self._task is not None and not self._task.done():
                return
            self._stop_event.clear()
            if self._task_manager is not None:
                self._task = self._task_manager.create(
                    self._run(),
                    name="qbit-sync-loop",
                )
                return
            self._task = BGTaskManager.spawn(self._run(), name="qbit-sync-loop")

    async def stop(self):
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass

    async def replace_qbit_client(self, new_qbit: QBitSyncClient) -> None:
        """Align future sync polls with a newly committed qB adapter."""
        async with self._lock:
            self._qbit = new_qbit

    async def _run(self):
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._backoff.next_delay())
                break
            except asyncio.TimeoutError:
                pass

            async with self._lock:
                qbit = self._qbit
            store = self._store
            if qbit is None:
                log.warning("qB 同步跳过：qbit client 为 None（可能尚未配置）")
                continue
            if store is None:
                log.error("qB 同步跳过：store 为 None，数据持久化不可用")
                continue

            try:
                snapshot, removed_hashes = await qbit.poll_torrent_snapshot_with_removed()
            except Exception as e:
                self._backoff.record_failure()
                log.debug(
                    "qB 状态同步失败，将退避到 %.1fs 后重试: %s", self._backoff.next_delay(), e
                )
                continue
            self._backoff.record_success()

            tracked_items = await call_store(
                store,
                "list",
                status=_SYNC_ACTIVE_STATUSES,
                limit=_MAX_STORE_ITEMS,
            )
            if len(tracked_items) >= _MAX_STORE_ITEMS:
                log.error(
                    "tracked items 达到截断上限 %d，部分 item 可能未被同步", len(tracked_items)
                )
                await self._bus.emit(
                    Event(
                        EventType.ERROR,
                        {
                            "error": "store_limit_reached",
                            "message": (
                                f"tracked items 达到截断上限 {_MAX_STORE_ITEMS}，"
                                "超过上限的 item 将不被同步"
                            ),
                            "count": len(tracked_items),
                        },
                    )
                )
            if not tracked_items:
                continue

            progress = _ReconcileProgress()
            try:
                await asyncio.wait_for(
                    self._reconcile_batch(tracked_items, snapshot, removed_hashes, progress),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                remaining = max(len(tracked_items) - progress.processed, 0)
                log.warning(
                    "同步轮次超时（60s），已处理 %d 条，跳过剩余 %d 条",
                    progress.processed,
                    remaining,
                )

    async def _reconcile_batch(
        self,
        tracked_items: list,
        snapshot: dict,
        removed_hashes: set[str],
        progress: _ReconcileProgress | None = None,
    ):
        """逐条 reconcile tracked items，每次 reconcile 前后检查 stop 信号。"""
        for item in tracked_items:
            if self._stop_event.is_set():
                break

            hash_key = item.hash
            torrent = snapshot.get(hash_key.lower())
            is_removed = hash_key.lower() in removed_hashes

            try:
                await self._transitions.reconcile_download_snapshot(
                    hash_key,
                    item,
                    torrent,
                    was_removed=is_removed,
                )
            except Exception as e:
                log.error(
                    "reconcile_download_snapshot 失败 for %s: %s",
                    hash_key,
                    e,
                )

            if progress is not None:
                progress.processed += 1

            if self._stop_event.is_set():
                break
