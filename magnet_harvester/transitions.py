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
