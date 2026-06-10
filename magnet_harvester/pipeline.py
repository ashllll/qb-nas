"""
HarvestPipeline — 爬取→分类→下载管道（深模块）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Callable, List, Optional, Protocol, runtime_checkable

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus

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
    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool: ...
    async def ping(self) -> bool: ...
    def close(self): ...
    def is_healthy(self) -> bool: ...


# ── HarvestPipeline ──────────────────────────

class HarvestPipeline:
    def __init__(self, crawler: CrawlPhase, classifier: ClassifyPhase, qbit: DownloadPhase, store: Any, bus: MessageBus):
        self._crawler = crawler
        self._classifier = classifier
        self._qbit = qbit
        self._store = store
        self._bus = bus

    async def _emit_item_changed(self, hash_key: str):
        item = self._store.get(hash_key)
        if item is not None:
            await self._bus.emit(Event(EventType.STORE_CHANGED, {"item": item.model_dump()}))

    async def execute(self, url: str, depth: int = 1, auto_download: bool = False):
        await self._bus.emit(Event(EventType.CRAWL_START, {"url": url}))
        new_hashes: List[str] = []

        async for msg in self._crawler.crawl(url, depth=depth):
            t = msg["type"]
            if t == "found":
                item = MagnetItem(**msg["item"])
                if self._store.add(item):
                    new_hashes.append(item.hash)
                    await self._bus.emit(Event(EventType.MAGNET_FOUND, {"item": item.model_dump()}))
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
            self._store.update(item.hash, status=TaskStatus.classifying, error_msg=None)
            await self._emit_item_changed(item.hash)

        await self._bus.emit(Event(EventType.CLASSIFY_START, {"count": len(items)}))

        def on_result(index: int, result: dict):
            h = index_to_hash.get(index)
            if h:
                self._store.update(
                    h,
                    category=result["category"],
                    save_path=result["save_path"],
                    status=TaskStatus.pending,
                    progress=0.0,
                    torrent_state=None,
                    error_msg=None,
                )
                event = Event(EventType.CLASSIFY_DONE, {
                    "hash": h, "category": result["category"],
                    "confidence": result.get("confidence", ""), "reason": result.get("reason", ""),
                })
                asyncio.create_task(self._bus.emit(event))
                asyncio.create_task(self._emit_item_changed(h))

        await self._classifier.classify_stream_batch(classify_input, on_result=on_result)
        await self._bus.emit(Event(EventType.CLASSIFY_ALL_DONE, {}))

    async def _download_items(self, hashes: List[str]):
        success = 0
        for i, h in enumerate(hashes):
            item = self._store.get(h)
            if not item or not item.category:
                continue
            self._store.update(h, status=TaskStatus.adding, progress=0.0, torrent_state="submitting", error_msg=None)
            await self._emit_item_changed(h)
            await self._bus.emit(Event(EventType.DOWNLOAD_START, {"hash": h, "name": item.name}))
            try:
                ok = await self._qbit.add_magnet(item.magnet, item.category, item.save_path or "")
                status = TaskStatus.queued if ok else TaskStatus.error
                if ok:
                    success += 1
                    self._store.update(h, torrent_state="submitted", progress=0.0)
                else:
                    self._store.update(h, error_msg=self._qbit.last_error or "qB 返回失败")
            except Exception as e:
                status = TaskStatus.error
                self._store.update(h, error_msg=str(e))
            self._store.update(h, status=status)
            await self._emit_item_changed(h)
            item_updated = self._store.get(h)
            await self._bus.emit(Event(EventType.DOWNLOAD_RESULT, {
                "hash": h, "status": status.value,
                "error_msg": item_updated.error_msg if item_updated else None,
                "progress": item_updated.progress if item_updated else 0.0,
                "torrent_state": item_updated.torrent_state if item_updated else None,
            }))
            if i > 0:
                await asyncio.sleep(0.3)

    async def reclassify(self, hashes: List[str]):
        items = [self._store.get(h) for h in hashes]
        items = [i for i in items if i is not None]
        await self._stream_classify(items)

    async def download(self, hashes: List[str]):
        await self._download_items(hashes)
