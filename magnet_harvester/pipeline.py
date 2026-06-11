"""
HarvestPipeline — 爬取→分类→下载管道（深模块）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Callable, List, Optional, Protocol, runtime_checkable

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import ItemStore

log = logging.getLogger(__name__)


# ── Phase Protocols ──────────────────────────

@runtime_checkable
class CrawlPhase(Protocol):
    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]: ...


@runtime_checkable
class ClassifyPhase(Protocol):
    async def classify_stream_batch(self, items: List[dict], on_result: Callable[[int, dict], None] | None = None) -> None: ...
    @property
    def usage(self) -> Any: ...
    def get_cache_stats(self) -> dict: ...


@runtime_checkable
class DownloadPhase(Protocol):
    last_error: str | None
    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool: ...
    async def ping(self) -> bool: ...
    def close(self): ...
    def is_healthy(self) -> bool: ...


# ── HarvestPipeline ──────────────────────────

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

    async def _emit_download_result(self, hash_key: str, status: TaskStatus):
        item = self._store.get(hash_key)
        await self._bus.emit(Event(EventType.DOWNLOAD_RESULT, {
            "hash": hash_key,
            "status": status.value,
            "error_msg": item.error_msg if item else None,
            "progress": item.progress if item else 0.0,
            "torrent_state": item.torrent_state if item else None,
        }))


class HarvestPipeline:
    def __init__(
        self,
        crawler: CrawlPhase,
        classifier: ClassifyPhase,
        qbit: DownloadPhase,
        store: ItemStore,
        bus: MessageBus,
        task_manager: BackgroundTaskSpawner | None = None,
    ):
        self._crawler = crawler
        self._classifier = classifier
        self._qbit = qbit
        self._store = store
        self._bus = bus
        self._task_manager = task_manager
        self._transitions = MagnetItemTransitions(store=store, bus=bus)

    def _spawn(self, coro, *, name: str | None = None) -> asyncio.Task:
        if self._task_manager is not None:
            return self._task_manager.create(coro, name=name)
        return asyncio.create_task(coro, name=name)

    async def execute(self, url: str, depth: int = 1, auto_download: bool = False):
        await self._bus.emit(Event(EventType.CRAWL_START, {"url": url}))
        new_hashes: List[str] = []

        async for msg in self._crawler.crawl(url, depth=depth):
            t = msg["type"]
            if t == "found":
                item = MagnetItem(**msg["item"])
                if await self._transitions.found(item):
                    new_hashes.append(item.hash)
            elif t == "progress":
                await self._bus.emit(Event(EventType.CRAWL_PROGRESS, msg))
            elif t == "error":
                await self._bus.emit(Event(EventType.CRAWL_ERROR, msg))
            elif t == "done":
                await self._bus.emit(Event(EventType.CRAWL_DONE, {"total": msg["total"], "url": msg["url"]}))

        if not new_hashes:
            return

        items = [self._store.get(h) for h in new_hashes]
        items = [i for i in items if i is not None]
        await self._stream_classify(items)

        if auto_download:
            await self._download_items(new_hashes)

    async def _stream_classify(self, items: List[MagnetItem]):
        if not items:
            return
        index_to_hash = {i: item.hash for i, item in enumerate(items)}
        classify_input = [{"index": i, "name": item.name} for i, item in enumerate(items)]

        for item in items:
            await self._transitions.classification_started(item.hash)

        await self._bus.emit(Event(EventType.CLASSIFY_START, {"count": len(items)}))
        result_events: list[asyncio.Task] = []

        def on_result(index: int, result: dict):
            h = index_to_hash.get(index)
            if h:
                result_events.append(
                    self._spawn(
                        self._transitions.classified(h, result),
                        name=f"classify:{h}",
                    )
                )

        await self._classifier.classify_stream_batch(classify_input, on_result=on_result)
        if result_events:
            await asyncio.gather(*result_events)
        await self._bus.emit(Event(EventType.CLASSIFY_ALL_DONE, {}))

    async def _download_items(self, hashes: List[str]):
        for i, h in enumerate(hashes):
            item = self._store.get(h)
            if not item or not item.category:
                continue
            await self._transitions.download_submitting(h)
            try:
                ok = await self._qbit.add_magnet(item.magnet, item.category, item.save_path or "")
                if ok:
                    await self._transitions.download_submitted(h)
                else:
                    await self._transitions.download_failed(h, self._qbit.last_error or "qB 返回失败")
            except Exception as e:
                await self._transitions.download_failed(h, str(e))
            if i > 0:
                await asyncio.sleep(0.3)

    async def reclassify(self, hashes: List[str]):
        items = [self._store.get(h) for h in hashes]
        items = [i for i in items if i is not None]
        await self._stream_classify(items)

    async def download(self, hashes: List[str]):
        await self._download_items(hashes)

    def replace_download_phase(self, new_qbit: DownloadPhase):
        """Hot-swap the download phase (e.g. when qBittorrent config changes)."""
        self._qbit = new_qbit
