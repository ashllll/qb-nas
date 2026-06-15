"""Shared user action execution for HTTP routes and agent tools."""
from __future__ import annotations

import asyncio
from typing import Protocol

from magnet_harvester.context.app_context import BackgroundTaskSpawner, StatsTracker
from magnet_harvester.item_transitions import MagnetItemTransitions
from magnet_harvester.store import ItemStore


class UserActionPipeline(Protocol):
    async def execute(self, url: str, depth: int = 1, auto_download: bool = False): ...
    async def admit_crawl_target(self, url: str) -> str: ...
    async def download(self, hashes: list[str]): ...
    async def reclassify(self, hashes: list[str]): ...


class UserActionExecutor:
    """Executes shared user actions behind one interface."""

    def __init__(
        self,
        store: ItemStore,
        pipeline: UserActionPipeline | None,
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
        if self._task_manager is not None:
            return self._task_manager.create(coro, name=name)
        return asyncio.create_task(coro, name=name)

    async def start_crawl(self, url: str, *, depth: int = 1, auto_download: bool = False) -> dict:
        if self._pipeline is None:
            return {"status": "error", "reason": "pipeline unavailable"}

        url = url.strip()
        if not url:
            return {"status": "error", "reason": "url 不能为空"}
        await self._pipeline.admit_crawl_target(url)
        depth = max(1, min(int(depth), 3))
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
