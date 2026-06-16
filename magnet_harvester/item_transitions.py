"""Magnet item state transitions and observable events."""
from __future__ import annotations

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import ItemStore


class MagnetItemTransitions:
    """Applies Magnet item state changes and publishes matching events."""

    def __init__(self, store: ItemStore, bus: MessageBus):
        self._store = store
        self._bus = bus

    async def _emit_item_changed(self, hash_key: str):
        item = self._store.get(hash_key)
        if item is not None:
            await self._bus.emit(Event(EventType.STORE_CHANGED, {"item": item.model_dump()}))

    async def found(self, item: MagnetItem) -> bool:
        if not self._store.add(item):
            return False
        await self._bus.emit(Event(EventType.MAGNET_FOUND, {"item": item.model_dump()}))
        return True

    async def clipboard_found(self, item: MagnetItem) -> bool:
        if not await self.found(item):
            return False
        await self._emit_item_changed(item.hash)
        return True

    async def classification_started(self, hash_key: str):
        self._store.update(hash_key, status=TaskStatus.classifying, error_msg=None)
        await self._emit_item_changed(hash_key)

    async def classified(self, hash_key: str, result: dict):
        self._store.update(
            hash_key,
            category=result["category"],
            save_path=result["save_path"],
            status=TaskStatus.pending,
            progress=0.0,
            torrent_state=None,
            error_msg=None,
        )
        await self._bus.emit(Event(EventType.CLASSIFY_DONE, {
            "hash": hash_key,
            "category": result["category"],
            "confidence": result.get("confidence", ""),
            "reason": result.get("reason", ""),
        }))
        await self._emit_item_changed(hash_key)

    async def manually_classified(self, hash_key: str, category: str) -> bool:
        if not self._store.update(hash_key, category=category, save_path=""):
            return False
        await self._bus.emit(
            Event(
                EventType.CLASSIFY_DONE,
                {
                    "hash": hash_key,
                    "category": category,
                    "confidence": "manual",
                    "reason": "手动修改",
                },
            )
        )
        await self._emit_item_changed(hash_key)
        return True

    async def download_submitting(self, hash_key: str):
        item = self._store.get(hash_key)
        if item is None:
            return
        self._store.update(
            hash_key,
            status=TaskStatus.adding,
            progress=0.0,
            torrent_state="submitting",
            error_msg=None,
        )
        await self._emit_item_changed(hash_key)
        await self._bus.emit(Event(EventType.DOWNLOAD_START, {"hash": hash_key, "name": item.name}))

    async def download_submitted(self, hash_key: str):
        self._store.update(
            hash_key,
            status=TaskStatus.queued,
            torrent_state="submitted",
            progress=0.0,
            error_msg=None,
        )
        await self._emit_item_changed(hash_key)
        await self._emit_download_result(hash_key, TaskStatus.queued)

    async def download_failed(self, hash_key: str, error_msg: str):
        self._store.update(hash_key, status=TaskStatus.error, error_msg=error_msg)
        await self._emit_item_changed(hash_key)
        await self._emit_download_result(hash_key, TaskStatus.error)

    async def download_removed(self, hash_key: str, previous_status: TaskStatus | None):
        self._store.update(
            hash_key,
            status=TaskStatus.error,
            error_msg="种子已从 qBittorrent 中消失",
            torrent_state="removed",
        )
        await self.download_state_changed(hash_key, previous_status)

    async def download_status_changed(
        self,
        hash_key: str,
        *,
        fields: dict,
        previous_status: TaskStatus | None,
    ):
        if not fields:
            return
        if not self._store.update(hash_key, **fields):
            return
        await self.download_state_changed(hash_key, previous_status)

    async def download_state_changed(
        self,
        hash_key: str,
        previous_status: TaskStatus | None = None,
    ):
        await self._emit_item_changed(hash_key)
        item = self._store.get(hash_key)
        if item is None:
            return

        is_terminal = item.status in {TaskStatus.success, TaskStatus.error}
        is_new_phase = previous_status in {
            TaskStatus.pending,
            TaskStatus.adding,
            TaskStatus.classifying,
            None,
        }
        if is_terminal or is_new_phase:
            await self._emit_download_result(hash_key, item.status)

    async def cleared(self) -> int:
        count = self._store.count
        self._store.clear()
        await self._bus.emit(Event(EventType.ITEMS_CLEARED, {"type": "items_cleared"}))
        return count

    async def _emit_download_result(self, hash_key: str, status: TaskStatus):
        item = self._store.get(hash_key)
        await self._bus.emit(Event(EventType.DOWNLOAD_RESULT, {
            "hash": hash_key,
            "status": status.value,
            "error_msg": item.error_msg if item else None,
            "progress": item.progress if item else 0.0,
            "torrent_state": item.torrent_state if item else None,
        }))
