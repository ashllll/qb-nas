"""Shared user action execution for HTTP routes and agent tools."""
from __future__ import annotations

import asyncio
from typing import Protocol

from magnet_harvester.context.app_context import BackgroundTaskSpawner, StatsTracker
from magnet_harvester.pipeline import PipelineProtocol
from magnet_harvester.store import ItemStore
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.utils.bg_tasks import BGTaskManager
from magnet_harvester.utils.serializers import item_summary


class UserActionExecutor:
    """Executes shared user actions behind one interface."""

    def __init__(
        self,
        store: ItemStore,
        pipeline: PipelineProtocol | None,
        task_manager: BackgroundTaskSpawner | None,
        transitions: MagnetItemTransitions,
        stats: StatsTracker | None = None,
    ):
        self._store = store
        self._pipeline = pipeline
        self._task_manager = task_manager
        self._transitions = transitions
        self._stats = stats

    def _spawn(self, coro, *, name: str):
        return BGTaskManager.spawn(coro, task_manager=self._task_manager, name=name)

    # ── 查询方法（原 ToolExecutor 的 store 直读操作）──

    def get_stats(self) -> dict:
        s = self._store.stats()
        return {"total": s.total, "by_category": s.by_category, "by_status": s.by_status}

    def list_items(self, *, category: str | None = None, status: str = "all", limit: int = 20) -> dict:
        items = self._store.list(category=category, status=status, limit=limit)
        return {"count": len(items), "items": [item_summary(i) for i in items]}

    def search_items(self, *, query: str, limit: int = 20) -> dict:
        hits = self._store.search(query)
        return {"count": len(hits), "results": [item_summary(i) for i in hits[:limit]]}

    # ── 操作方法 ──

    async def start_crawl(self, url: str, *, depth: int = 1, auto_download: bool = False) -> dict:
        if self._pipeline is None:
            return {"status": "error", "reason": "pipeline unavailable"}

        url = url.strip()
        if not url:
            return {"status": "error", "reason": "url 不能为空"}
        try:
            await self._pipeline.admit_crawl_target(url)
        except ValueError as exc:
            return {"status": "error", "reason": str(exc)}
        depth = max(1, min(int(depth), 3, self._pipeline.max_crawl_depth()))
        if self._stats is not None:
            self._stats.record_crawl()
        self._spawn(
            self._pipeline.execute(url, depth=depth, auto_download=auto_download),
            name=f"crawl:{url[:40]}",
        )
        return {"status": "started", "url": url, "depth": depth}

    async def download(self, hashes: list[str], *, task_name: str = "download_selected") -> dict:
        if self._pipeline is None:
            return {"status": "error", "reason": "pipeline unavailable"}

        if self._stats is not None:
            self._stats.record_download()
        self._spawn(self._pipeline.download(hashes), name=task_name)
        return {"status": "started", "count": len(hashes)}

    async def download_pending(self) -> dict:
        pending = self._store.get_pending()
        hashes = [item.hash for item in pending]
        return await self.download(hashes, task_name="download_batch")

    async def reclassify(self, hashes: list[str]) -> dict:
        if self._pipeline is None:
            return {"status": "error", "reason": "pipeline unavailable"}

        self._spawn(self._pipeline.reclassify(hashes), name="reclassify")
        return {"status": "started"}

    async def manually_reclassify(self, hash_prefix: str, category: str) -> dict:
        if len(hash_prefix) < 8:
            return {"status": "error", "reason": "hash 至少需要 8 位前缀"}

        matches = self._store.get_hashes_by_prefix(hash_prefix)
        if not matches:
            return {"status": "not_found", "hash": hash_prefix}

        match = matches[0]
        await self._transitions.manually_classified(match, category)
        return {"status": "ok", "hash": match, "new_category": category}

    async def clear_items(self) -> dict:
        count = await self._transitions.cleared()
        return {"status": "cleared", "removed": count}
