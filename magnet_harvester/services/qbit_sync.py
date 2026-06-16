"""
QBitSyncLoop — background polling service for qBittorrent state sync.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from magnet_harvester.bus import MessageBus
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.models import TaskStatus
from magnet_harvester.store import ItemStore

log = logging.getLogger(__name__)


class QBitSyncClient(Protocol):
    async def poll_torrent_snapshot(self) -> dict: ...
    def take_recently_removed(self) -> set[str]: ...
    def map_torrent_status(self, torrent) -> dict: ...


class QBitSyncLoop:
    """Polls qBittorrent state and reconciles tracked items."""

    def __init__(
        self,
        qbit_client: QBitSyncClient,
        store: ItemStore,
        bus: MessageBus,
        poll_interval: float = 2.0,
        task_manager: BackgroundTaskSpawner | None = None,
        transitions: MagnetItemTransitions | None = None,
    ):
        self._qbit = qbit_client
        self._store = store
        self._bus = bus
        self._poll_interval = poll_interval
        self._task_manager = task_manager
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._transitions = transitions or MagnetItemTransitions(store=store, bus=bus)

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

    async def replace_qbit_client(self, new_qbit: QBitSyncClient) -> None:
        """Align future sync polls with a newly committed qB adapter."""
        async with self._lock:
            self._qbit = new_qbit

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
                for item in store.list(limit=store.count)
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
                        await self._transitions.download_removed(hash_key, previous_status)
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
                    await self._transitions.download_status_changed(
                        hash_key,
                        fields=fields,
                        previous_status=previous_status,
                    )
