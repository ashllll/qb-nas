"""
ToolExecutor — dispatches agent tool calls to store/pipeline operations.
"""
from __future__ import annotations

from typing import Protocol

from magnet_harvester.bus import MessageBus
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.item_transitions import MagnetItemTransitions
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.store import ItemStore
from magnet_harvester.utils.serializers import _item_summary

class PipelineToolTarget(Protocol):
    async def execute(self, url: str, depth: int = 1, auto_download: bool = False): ...
    async def admit_crawl_target(self, url: str) -> str: ...
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
        transitions: MagnetItemTransitions | None = None,
        action_executor: UserActionExecutor | None = None,
    ):
        self._store = store
        self._pipeline = pipeline
        self._bus = bus
        self._task_manager = task_manager
        self._transitions = transitions or MagnetItemTransitions(store=store, bus=bus)
        self._actions = action_executor or UserActionExecutor(
            store=store,
            pipeline=pipeline,
            task_manager=task_manager,
            transitions=self._transitions,
        )

    async def execute(self, name: str, inp: dict) -> dict:
        store = self._store

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
            try:
                return await self._actions.start_crawl(
                    url,
                    depth=int(inp.get("depth", 1)),
                    auto_download=False,
                )
            except ValueError as exc:
                return {"status": "error", "reason": str(exc)}

        if name == "add_to_queue":
            hashes = inp.get("hashes", [])
            if hashes == ["all"]:
                return await self._actions.download_pending()
            return await self._actions.download(hashes, task_name="download_batch")

        if name == "reclassify_item":
            h = inp.get("hash", "")
            cat = inp.get("category", "")
            if len(h) < 8:
                return {"status": "error", "reason": "hash 至少需要 8 位前缀"}
            return await self._actions.manually_reclassify(h, cat)

        if name == "search_items":
            query = inp.get("query", "")
            hits = store.search(query)
            return {"count": len(hits), "results": [_item_summary(i) for i in hits[:20]]}

        if name == "clear_all":
            if not inp.get("confirm"):
                return {"status": "cancelled", "reason": "需要 confirm=true"}
            return await self._actions.clear_items()

        return {"error": f"未知工具: {name}"}
