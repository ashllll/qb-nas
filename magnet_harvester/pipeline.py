"""
HarvestPipeline — 爬取→分类→下载管道（深模块）

接口: execute(url, depth, auto_download) → AsyncIterable[Event]
内部阶段 (crawl/classify/download) 是内部缝 — 调用者看不到。
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, List

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.classifier import MiniMaxClassifier
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.tts_client import MinimaxTTS

log = logging.getLogger(__name__)


class HarvestPipeline:
    """管道编排器。

    接口: execute(url, depth, auto_download)
    — 1 个入口，整个管道的复杂性隐藏在后面。

    依赖通过构造函数注入（缝）：
    - crawler / classifier / qbit / tts: 服务适配器
    - store: ItemStore 适配器（InMemory 或 Redis）
    - bus: MessageBus 适配器（prod 或 NullBus）
    """

    def __init__(
        self,
        crawler: MagnetCrawler,
        classifier: MiniMaxClassifier,
        qbit: QBittorrentClient,
        tts: MinimaxTTS,
        store: InMemoryItemStore,
        bus: MessageBus,
    ):
        self._crawler = crawler
        self._classifier = classifier
        self._qbit = qbit
        self._tts = tts
        self._store = store
        self._bus = bus

    async def execute(self, url: str, depth: int = 1, auto_download: bool = False):
        """执行完整的爬取→分类→下载管道。

        产出 Event 对象流，每个事件通过 MessageBus 广播。
        """
        await self._bus.emit_nowait(Event(EventType.CRAWL_START, {"url": url}))

        new_hashes: List[str] = []

        # ── Phase 1: Crawl ──────────────────────
        async for msg in self._crawler.crawl(url, depth=depth):
            t = msg["type"]
            if t == "found":
                item = MagnetItem(**msg["item"])
                if self._store.add(item):
                    new_hashes.append(item.hash)
                    await self._bus.emit_nowait(Event(EventType.MAGNET_FOUND, {"item": item.model_dump()}))
            elif t == "progress":
                await self._bus.emit_nowait(Event(EventType.CRAWL_PROGRESS, msg))
            elif t == "error":
                await self._bus.emit_nowait(Event(EventType.CRAWL_ERROR, msg))
            elif t == "done":
                total = msg["total"]
                await self._bus.emit_nowait(Event(EventType.CRAWL_DONE, {"total": total, "url": msg["url"]}))
                await self._tts.notify("crawl_done", total=total)

        if not new_hashes:
            return

        # ── Phase 2: Classify ───────────────────
        items = [self._store.get(h) for h in new_hashes]
        items = [i for i in items if i is not None]
        await self._stream_classify(items)

        if not auto_download:
            return

        # ── Phase 3: Download ───────────────────
        await self._download_items(new_hashes)

    async def _stream_classify(self, items: List[MagnetItem]):
        if not items:
            return
        index_to_hash = {i: item.hash for i, item in enumerate(items)}
        classify_input = [{"index": i, "name": item.name} for i, item in enumerate(items)]

        await self._bus.emit_nowait(Event(EventType.CLASSIFY_START, {"count": len(items)}))

        def on_result(index: int, result: dict):
            h = index_to_hash.get(index)
            if h:
                self._store.update(
                    h,
                    category=result["category"],
                    save_path=result["save_path"],
                    status=TaskStatus.pending,
                )
                event = Event(EventType.CLASSIFY_DONE, {
                    "hash": h,
                    "category": result["category"],
                    "confidence": result.get("confidence", ""),
                    "reason": result.get("reason", ""),
                })
                asyncio.create_task(self._bus.emit(event))

        await self._classifier.classify_stream_batch(classify_input, on_result=on_result)

        await self._bus.emit_nowait(Event(EventType.USAGE_UPDATE, {"data": self._classifier.usage.as_dict()}))
        await self._bus.emit_nowait(Event(EventType.CLASSIFY_ALL_DONE, {}))

    async def _download_items(self, hashes: List[str]):
        success = 0
        for h in hashes:
            item = self._store.get(h)
            if not item or not item.category:
                continue
            self._store.update(h, status=TaskStatus.adding)
            await self._bus.emit_nowait(Event(EventType.DOWNLOAD_START, {"hash": h, "name": item.name}))
            try:
                ok = await self._qbit.add_magnet(item.magnet, item.category, item.save_path or "")
                status = TaskStatus.success if ok else TaskStatus.error
                if ok:
                    success += 1
            except Exception as e:
                status = TaskStatus.error
                self._store.update(h, error_msg=str(e))
            self._store.update(h, status=status)
            await self._bus.emit_nowait(Event(EventType.DOWNLOAD_RESULT, {"hash": h, "status": status.value}))

        if success:
            await self._tts.notify("download_done", count=success)

    async def reclassify(self, hashes: List[str]):
        """重新分类指定条目（不跑爬取/下载）"""
        items = [self._store.get(h) for h in hashes]
        items = [i for i in items if i is not None]
        await self._stream_classify(items)

    async def download(self, hashes: List[str]):
        """只下载指定条目（不跑爬取/分类）"""
        await self._download_items(hashes)
