"""
QBitSyncLoop — background polling service for qBittorrent state sync.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from magnet_harvester.bus import MessageBus
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.store import ItemStore
from magnet_harvester.transitions import DownloadTransitions
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)

# 每轮取多少条做对账。分页 + 游标让 50k 活跃条目也不会每轮从头重扫，
# 单轮 60s 超时只跳过本页剩余，下一轮从断点继续。
_SYNC_PAGE_SIZE = 2000


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
    last_hash: str | None = None


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
        downloads: DownloadTransitions | None = None,
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
        self._downloads = downloads or DownloadTransitions(store=store, bus=bus)
        self._sync_cursor: str | None = None
        # qB 的「本轮删除」只在这一轮返回；分页时被删的尾部条目可能不在当前页，
        # 攒到它被翻到再消费，整轮走完（游标回 None）才清空。
        self._removed_pending: set[str] = set()

    async def start(self):
        async with self._lock:
            if self._task is not None and not self._task.done():
                return
            self._stop_event.clear()
            self._task = BGTaskManager.spawn(
                self._run(),
                task_manager=self._task_manager,
                name="qbit-sync-loop",
            )

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

            # 一轮里的任何异常都只影响这一轮：循环本身死了不会自动重启，
            # 用户从此看不到状态更新（qB 重启、SQLite 偶发 locked 都能触发）。
            try:
                await self._sync_round(qbit, store)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._backoff.record_failure()
                log.exception("qB 状态同步轮次失败，下一轮继续")
            else:
                self._backoff.record_success()

    async def _sync_round(self, qbit: QBitSyncClient, store: ItemStore) -> None:
        snapshot, removed_hashes = await qbit.poll_torrent_snapshot_with_removed()
        self._removed_pending.update(str(hash_key).lower() for hash_key in removed_hashes)
        tracked_items = await store.list_for_sync(
            after_hash=self._sync_cursor,
            limit=_SYNC_PAGE_SIZE,
        )
        if not tracked_items:
            self._sync_cursor = None
            self._removed_pending.clear()
            return

        progress = _ReconcileProgress()
        try:
            await asyncio.wait_for(
                self._reconcile_batch(tracked_items, snapshot, self._removed_pending, progress),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            remaining = max(len(tracked_items) - progress.processed, 0)
            log.warning(
                "同步轮次超时（60s），已处理 %d 条，跳过剩余 %d 条",
                progress.processed,
                remaining,
            )
        finally:
            # 页未满才算走完；页满/超时都从最后一条继续，避免尾部永远轮不到。
            page_exhausted = progress.processed >= len(tracked_items)
            self._sync_cursor = (
                progress.last_hash
                if progress.last_hash is not None
                and (not page_exhausted or len(tracked_items) >= _SYNC_PAGE_SIZE)
                else None
            )
            if self._sync_cursor is None:
                self._removed_pending.clear()

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

            handled = False
            try:
                await self._downloads.reconcile_snapshot(
                    hash_key,
                    item,
                    torrent,
                    was_removed=is_removed,
                )
                handled = True
            except Exception as e:
                log.error(
                    "reconcile_download_snapshot 失败 for %s: %s",
                    hash_key,
                    e,
                )
            if handled:
                removed_hashes.discard(hash_key.lower())

            if progress is not None:
                progress.processed += 1
                progress.last_hash = hash_key

            if self._stop_event.is_set():
                break
