"""
MagnetItemTransitions — applies Magnet item state changes and publishes events.

This module encapsulates the knowledge of what state transitions exist for a
Magnet item and which events each transition should publish.

Used by HarvestPipeline during crawl→classify→download orchestration.
"""
from __future__ import annotations

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.item_events import ItemEventEmitter
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import ItemStore


class MagnetItemTransitions:
    """Applies Magnet item state changes and publishes matching events.

    Each method performs a store update + event emission as an atomic
    conceptual transition. The event rules are delegated to ItemEventEmitter.
    """

    def __init__(self, store: ItemStore, bus: MessageBus):
        self._store = store
        self._bus = bus
        self._events = ItemEventEmitter(store=store, bus=bus)

    async def found(self, item: MagnetItem) -> bool:
        if not self._store.add(item):
            return False
        await self._bus.emit(Event(EventType.MAGNET_FOUND, {"item": item.model_dump()}))
        return True

    async def classification_started(self, hash_key: str):
        self._store.update(hash_key, status=TaskStatus.classifying, error_msg=None)
        await self._events.emit_item_changed(hash_key)

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
        await self._events.emit_item_changed(hash_key)

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
        await self._events.emit_item_changed(hash_key)
        await self._bus.emit(Event(EventType.DOWNLOAD_START, {"hash": hash_key, "name": item.name}))

    async def download_submitted(self, hash_key: str):
        self._store.update(
            hash_key,
            status=TaskStatus.queued,
            torrent_state="submitted",
            progress=0.0,
            error_msg=None,
        )
        await self._events.emit_item_changed(hash_key)
        await self._events.emit_download_result(hash_key, previous_status=TaskStatus.adding)

    async def download_failed(self, hash_key: str, error_msg: str):
        self._store.update(hash_key, status=TaskStatus.error, error_msg=error_msg)
        await self._events.emit_item_changed(hash_key)
        await self._events.emit_download_result(hash_key, previous_status=TaskStatus.adding)

    # ── 以下方法来自 item_transitions.py（移植版，使用 ItemEventEmitter 委托）──

    async def clipboard_found(self, item: MagnetItem) -> bool:
        """found + emit_item_changed（剪贴板入口专用）"""
        if not await self.found(item):
            return False
        await self._events.emit_item_changed(item.hash)
        return True

    async def manually_classified(self, hash_key: str, category: str) -> bool:
        """手动分类：更新 + CLASSIFY_DONE + emit_item_changed"""
        if not self._store.update(hash_key, category=category, save_path=""):
            return False
        await self._bus.emit(Event(EventType.CLASSIFY_DONE, {
            "hash": hash_key,
            "category": category,
            "confidence": "manual",
            "reason": "手动修改",
        }))
        await self._events.emit_item_changed(hash_key)
        return True

    async def download_removed(self, hash_key: str, previous_status: TaskStatus | None):
        """种子已从 qBittorrent 中消失"""
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
        """同步 qB 状态：更新字段 + download_state_changed"""
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
        """emit_item_changed + 有条件 emit_download_result"""
        await self._events.emit_item_changed(hash_key)
        await self._events.emit_download_result(hash_key, previous_status)

    async def cleared(self) -> int:
        """清空全部 + ITEMS_CLEARED"""
        count = self._store.count
        self._store.clear()
        await self._bus.emit(Event(EventType.ITEMS_CLEARED, {"type": "items_cleared"}))
        return count
