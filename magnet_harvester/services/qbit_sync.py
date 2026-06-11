"""
QBitSyncLoop — background polling service for qBittorrent state sync.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.models import TaskStatus
from magnet_harvester.utils.serializers import _item_payload

log = logging.getLogger(__name__)


class QBitSyncLoop:
    """Polls qBittorrent state and reconciles tracked items."""

    def __init__(
        self,
        qbit_client: Any,
        store: Any,
        bus: MessageBus,
        poll_interval: float = 2.0,
        task_manager: BackgroundTaskSpawner | None = None,
    ):
        self._qbit = qbit_client
        self._store = store
        self._bus = bus
        self._poll_interval = poll_interval
        self._task_manager = task_manager
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self):
        if self._task_manager is not None:
            self._task = self._task_manager.create(
                self._run(),
                name="qbit-sync-loop",
            )
            return

        self._task = asyncio.create_task(self._run(), name="qbit-sync-loop")

    async def stop(self):
        self._stop_event.set()
        if self._task:
            await self._task

    async def _emit_store_changed(
        self, hash_key: str, previous_status: TaskStatus | None = None
    ):
        item = self._store.get(hash_key)
        if item is None:
            return

        await self._bus.emit(
            Event(EventType.STORE_CHANGED, {"item": _item_payload(item)})
        )

        if previous_status is not None and previous_status != item.status:
            await self._bus.emit(
                Event(
                    EventType.DOWNLOAD_RESULT,
                    {
                        "hash": hash_key,
                        "status": item.status.value,
                        "error_msg": item.error_msg,
                        "progress": item.progress,
                        "torrent_state": item.torrent_state,
                    },
                )
            )

    async def _run(self):
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
                break
            except asyncio.TimeoutError:
                pass

            qbit = self._qbit
            store = self._store
            if qbit is None or store is None:
                continue

            tracked_items = [
                item
                for item in store.list(limit=10000)
                if item.status
                in {TaskStatus.adding, TaskStatus.queued, TaskStatus.downloading}
            ]
            if not tracked_items:
                continue

            async with self._lock:
                if qbit is not self._qbit:
                    continue
                try:
                    snapshot = await qbit.poll_torrent_snapshot()
                    removed_hashes = qbit.take_recently_removed()
                except Exception as e:
                    log.debug(f"qB 状态同步失败: {e}")
                    continue

            for item in tracked_items:
                hash_key = item.hash
                torrent = snapshot.get(hash_key.lower())

                if torrent is None:
                    if (
                        hash_key.lower() in removed_hashes
                        and item.status != TaskStatus.success
                    ):
                        previous_status = item.status
                        store.update(
                            hash_key,
                            status=TaskStatus.error,
                            error_msg="种子已从 qBittorrent 中消失",
                            torrent_state="removed",
                        )
                        await self._emit_store_changed(
                            hash_key, previous_status=previous_status
                        )
                    continue

                mapped = qbit.map_torrent_status(torrent)
                previous_status = item.status
                fields: dict = {}

                if previous_status != mapped["status"]:
                    fields["status"] = mapped["status"]
                if item.progress != mapped["progress"]:
                    fields["progress"] = mapped["progress"]
                if item.torrent_state != mapped["torrent_state"]:
                    fields["torrent_state"] = mapped["torrent_state"]
                if item.error_msg and mapped["status"] != TaskStatus.error:
                    fields["error_msg"] = None

                if fields:
                    store.update(hash_key, **fields)
                    await self._emit_store_changed(
                        hash_key, previous_status=previous_status
                    )
