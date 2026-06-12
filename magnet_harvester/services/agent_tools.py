"""
ToolExecutor — dispatches agent tool calls to store/pipeline operations.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.store import ItemStore
from magnet_harvester.utils.serializers import _item_summary

log = logging.getLogger(__name__)


class PipelineToolTarget(Protocol):
    async def execute(self, url: str, depth: int = 1, auto_download: bool = False): ...
    async def download(self, hashes: list[str]): ...
    async def reclassify(self, hashes: list[str]): ...


class ToolExecutor:
    """Dispatches 7 agent tools to ItemStore / HarvestPipeline operations."""

    def __init__(
        self,
        store: ItemStore,
        pipeline: PipelineToolTarget | None,
        bus: MessageBus,
        task_manager: BackgroundTaskSpawner | None = None,
    ):
        self._store = store
        self._pipeline = pipeline
        self._bus = bus
        self._task_manager = task_manager

    def _spawn(self, coro, name: str):
        if self._task_manager is not None:
            return self._task_manager.create(coro, name=name)
        return asyncio.create_task(coro, name=name)

    async def execute(self, name: str, inp: dict) -> dict:
        store = self._store
        pipeline = self._pipeline

        if name == "get_stats":
            s = store.stats()
            return {"total": s.total, "by_category": s.by_category, "by_status": s.by_status}

        if name == "list_items":
            cat = inp.get("category")
            status = inp.get("status", "all")
            limit = int(inp.get("limit", 20))
            items = store.list(category=cat, status=status, limit=limit)
            return {"count": len(items), "items": [_item_summary(i) for i in items]}

        if name == "start_crawl":
            url = inp.get("url", "").strip()
            if not url:
                return {"status": "error", "reason": "url 不能为空"}
            depth = int(inp.get("depth", 1))
            depth = max(1, min(depth, 3))  # 限制深度 1-3，防止指数爆炸
            self._spawn(
                pipeline.execute(url, depth=depth, auto_download=False),
                name=f"crawl:{url[:40]}",
            )
            return {"status": "started", "url": url, "depth": depth}

        if name == "add_to_queue":
            hashes = inp.get("hashes", [])
            if hashes == ["all"]:
                pending = store.get_pending()
                hashes = [i.hash for i in pending]
            self._spawn(pipeline.download(hashes), name="download_batch")
            return {"status": "started", "count": len(hashes)}

        if name == "reclassify_item":
            h = inp.get("hash", "")
            cat = inp.get("category", "")
            if len(h) < 8:
                return {"status": "error", "reason": "hash 至少需要 8 位前缀"}
            matches = store.get_hashes_by_prefix(h)
            if matches:
                match = matches[0]
                store.update(match, category=cat, save_path="")
                await self._bus.emit(
                    Event(
                        EventType.CLASSIFY_DONE,
                        {
                            "hash": match,
                            "category": cat,
                            "confidence": "manual",
                            "reason": "手动修改",
                        },
                    )
                )
                return {"status": "ok", "hash": match, "new_category": cat}
            return {"status": "not_found", "hash": h}

        if name == "search_items":
            query = inp.get("query", "")
            hits = store.search(query)
            return {"count": len(hits), "results": [_item_summary(i) for i in hits[:20]]}

        if name == "clear_all":
            if not inp.get("confirm"):
                return {"status": "cancelled", "reason": "需要 confirm=true"}
            count = store.count
            store.clear()
            await self._bus.emit(Event(EventType.ITEMS_CLEARED, {"type": "items_cleared"}))
            return {"status": "cleared", "removed": count}

        return {"error": f"未知工具: {name}"}
