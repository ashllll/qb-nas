"""
HarvestPipeline — 爬取→分类→下载管道（深模块）

接口: execute(url, depth, auto_download)
内部阶段 (crawl/classify/download) 暴露为协议 — 调用者可注入任意实现。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Protocol, runtime_checkable

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# Phase Protocols — 每个阶段的可测试 seam
# ═══════════════════════════════════════════════════════

@runtime_checkable
class CrawlPhase(Protocol):
    """爬取阶段: URL → 磁力链接流"""
    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]: ...


@runtime_checkable
class ClassifyPhase(Protocol):
    """分类阶段: 磁力链接 → 分类结果"""
    async def classify_stream_batch(
        self, items: List[dict], on_result: Callable[[int, dict], None] | None = None
    ) -> None: ...
    @property
    def usage(self) -> Any: ...
    def get_cache_stats(self) -> dict: ...


@runtime_checkable
class DownloadPhase(Protocol):
    """下载阶段: 添加磁力链接到下载器"""
    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool: ...
    async def ping(self) -> bool: ...
    def close(self): ...
    def is_healthy(self) -> bool: ...


# ═══════════════════════════════════════════════════════
# HarvestPipeline — 管道编排器
# ═══════════════════════════════════════════════════════

class HarvestPipeline:
    """管道编排器。

    接口: execute(url, depth, auto_download)
    — 1 个入口，整个管道的复杂性隐藏在后面。

    依赖通过构造函数注入（缝）：
    - crawler / classifier / qbit / tts: 服务适配器（Phase Protocols）
    - store: ItemStore 适配器
    - bus: MessageBus 适配器
    """

    def __init__(
        self,
        crawler: CrawlPhase,
        classifier: ClassifyPhase,
        qbit: DownloadPhase,
        tts: Any,  # MinimaxTTS 或 duck-type（notify 方法）
        store: Any,  # ItemStore 实现
        bus: MessageBus,
    ):
        self._crawler = crawler
        self._classifier = classifier
        self._qbit = qbit
        self._tts = tts
        self._store = store
        self._bus = bus

    async def execute(self, url: str, depth: int = 1, auto_download: bool = False):
        """执行完整的爬取→分类→下载管道。"""
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
        for i, h in enumerate(hashes):
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
                elif not ok:
                    # 失败可能是重复，不加过多日志
                    pass
            except Exception as e:
                status = TaskStatus.error
                self._store.update(h, error_msg=str(e))
            self._store.update(h, status=status)
            await self._bus.emit_nowait(Event(EventType.DOWNLOAD_RESULT, {"hash": h, "status": status.value}))
            # 每批之间加 0.5s 间隔，防止 qB 拒接
            if i > 0 and i % 10 == 0:
                await asyncio.sleep(1)

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
