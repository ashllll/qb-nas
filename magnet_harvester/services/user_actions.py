"""Shared user action execution for HTTP routes and agent tools."""

from __future__ import annotations

import logging

from magnet_harvester.context.app_context import BackgroundTaskSpawner, StatsTracker
from magnet_harvester.pipeline import PipelineProtocol
from magnet_harvester.store import ItemStore
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)


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
        if self._task_manager is None:
            log.warning("task_manager 未配置，跳过后台任务: %s", name)
            return
        return BGTaskManager.spawn(coro, task_manager=self._task_manager, name=name)

    async def start_crawl(self, url: str, *, depth: int = 1, auto_download: bool = False) -> dict:
        if self._pipeline is None:
            return {"status": "error", "reason": "pipeline unavailable"}

        result = await self._pipeline.start_crawl(
            url,
            depth=depth,
            auto_download=auto_download,
        )
        if result.get("status") == "started" and self._stats is not None:
            self._stats.record_crawl()
        return result

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

        if len(matches) > 1:
            log.warning(
                "hash 前缀 %r 匹配到 %d 条记录，仅操作第一条 %r",
                hash_prefix, len(matches), matches[0],
            )

        match = matches[0]
        await self._transitions.manually_classified(match, category)
        return {"status": "ok", "hash": match, "new_category": category}

    async def clear_items(self) -> dict:
        count = await self._transitions.cleared()
        return {"status": "cleared", "removed": count}
