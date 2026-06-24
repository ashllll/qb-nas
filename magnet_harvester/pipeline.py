"""
HarvestPipeline — 爬取→分类→下载管道（深模块）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, List, Protocol, runtime_checkable

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.crawler import CrawlPhase
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import ItemStore
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)


# ── Phase Protocols ──────────────────────────


@runtime_checkable
class PipelineProtocol(Protocol):
    async def start_crawl(
        self, url: str, *, depth: int = 1, auto_download: bool = False
    ) -> dict: ...
    async def execute(self, url: str, depth: int = 1, auto_download: bool = False): ...
    async def admit_crawl_target(self, url: str) -> str: ...
    async def download(self, hashes: list[str]): ...
    async def reclassify(self, hashes: list[str]): ...
    def max_crawl_depth(self) -> int: ...


@runtime_checkable
class UsageStats(Protocol):
    def as_dict(self) -> dict: ...


@runtime_checkable
class ClassifyPhase(Protocol):
    async def classify_stream_batch(
        self, items: List[dict], on_result: Callable[[int, dict], None] | None = None
    ) -> None: ...
    @property
    def usage(self) -> UsageStats: ...
    def get_cache_stats(self) -> dict: ...


@runtime_checkable
class DownloadPhase(Protocol):
    last_error: str | None

    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool: ...
    async def ping(self) -> bool: ...
    def close(self): ...
    def is_healthy(self) -> bool: ...


class HarvestPipeline:
    def __init__(
        self,
        crawler: CrawlPhase,
        classifier: ClassifyPhase,
        qbit: DownloadPhase,
        store: ItemStore,
        bus: MessageBus,
        task_manager: BackgroundTaskSpawner | None = None,
        transitions: MagnetItemTransitions | None = None,
    ):
        self._crawler = crawler
        self._classifier = classifier
        self._qbit = qbit
        self._store = store
        self._bus = bus
        self._task_manager = task_manager
        self._transitions = transitions or MagnetItemTransitions(store=store, bus=bus)

    def _spawn(self, coro, *, name: str | None = None) -> asyncio.Task:
        return BGTaskManager.spawn(coro, task_manager=self._task_manager, name=name)

    async def start_crawl(
        self,
        url: str,
        *,
        depth: int = 1,
        auto_download: bool = False,
    ) -> dict:
        url = url.strip()
        if not url:
            return {"status": "error", "reason": "url 不能为空"}
        try:
            await self.admit_crawl_target(url)
        except ValueError as exc:
            return {"status": "error", "reason": str(exc)}

        effective_depth = max(1, min(int(depth), 3, self.max_crawl_depth()))
        task = self._spawn(
            self.execute(url, depth=effective_depth, auto_download=auto_download),
            name=f"crawl:{url[:40]}",
        )
        task_id = getattr(task, "task_id", task.get_name())
        return {"status": "started", "url": url, "depth": effective_depth, "task_id": task_id}

    async def admit_crawl_target(self, url: str) -> str:
        return await self._crawler.admit_url(url)

    def max_crawl_depth(self) -> int:
        return self._crawler.max_depth

    async def execute(self, url: str, depth: int = 1, auto_download: bool = False):
        await self._bus.emit(Event(EventType.CRAWL_START, {"url": url}))
        new_hashes: List[str] = []

        async for msg in self._crawler.crawl(url, depth=depth):
            t = msg["type"]
            if t == "found":
                try:
                    item = MagnetItem(**msg["item"])
                    if await self._transitions.found(item):
                        new_hashes.append(item.hash)
                except Exception:
                    log.exception("Crawl found handler failed for url=%s", url)
                    await self._bus.emit(
                        Event(
                            EventType.CRAWL_ERROR,
                            {"error": "found_handler_failed", "url": url, "msg": msg},
                        )
                    )
            elif t == "progress":
                await self._bus.emit(Event(EventType.CRAWL_PROGRESS, msg))
            elif t == "error":
                await self._bus.emit(Event(EventType.CRAWL_ERROR, msg))
            elif t == "done":
                await self._bus.emit(
                    Event(EventType.CRAWL_DONE, {"total": msg["total"], "url": msg["url"]})
                )

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

        await asyncio.gather(
            *[self._transitions.classification_started(item.hash) for item in items]
        )

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

    async def _download_items(self, hashes: List[str], concurrency: int = 3):
        semaphore = asyncio.Semaphore(concurrency)
        await asyncio.gather(*(self._download_single_item(h, semaphore) for h in hashes))

    async def _download_single_item(self, hash_key: str, semaphore: asyncio.Semaphore) -> None:
        item = self._store.get(hash_key)
        if not item or not item.category:
            return
        async with semaphore:
            await self._transitions.download_submitting(hash_key)
            try:
                ok = await self._qbit.add_magnet(
                    item.magnet, item.category, item.save_path or ""
                )
                if ok:
                    await self._transitions.download_submitted(hash_key)
                else:
                    await self._transitions.download_failed(
                        hash_key, self._qbit.last_error or "qB 返回失败"
                    )
            except Exception as e:
                await self._transitions.download_failed(hash_key, str(e))

    async def reclassify(self, hashes: List[str]):
        items = [self._store.get(h) for h in hashes]
        items = [
            i for i in items
            if i is not None and i.status not in {
                TaskStatus.adding, TaskStatus.queued,
                TaskStatus.downloading, TaskStatus.success,
            }
        ]
        if not items:
            return
        await self._stream_classify(items)

    async def download(self, hashes: List[str]):
        await self._download_items(hashes)

    def replace_download_phase(self, new_qbit: DownloadPhase):
        """Hot-swap the download phase (e.g. when qBittorrent config changes)."""
        self._qbit = new_qbit
