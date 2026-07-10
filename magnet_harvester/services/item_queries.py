"""Read-only Magnet item query helpers."""

from __future__ import annotations

from magnet_harvester.store import ItemStore, call_store
from magnet_harvester.utils.serializers import item_payload, item_summary


class ItemQueryExecutor:
    """Formats read-only Magnet item queries behind a small interface."""

    def __init__(self, store: ItemStore):
        self._store = store

    async def get_stats(self) -> dict:
        stats = await call_store(self._store, "stats")
        return {
            "total": stats.total,
            "by_category": stats.by_category,
            "by_status": stats.by_status,
        }

    async def list_items(
        self,
        *,
        category: str | None = None,
        status: str = "all",
        limit: int = 20,
    ) -> dict:
        items = await call_store(self._store, "list", category=category, status=status, limit=limit)
        return {"count": len(items), "items": [item_summary(item) for item in items]}

    async def page_items(
        self,
        *,
        category: str | None = None,
        status: str = "all",
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        limit = max(0, min(limit, 500))
        offset = max(0, min(offset, 10000))  # 硬上限，防止大 offset 导致内存 DoS
        total, items = await call_store(
            self._store,
            "count_and_page",
            category=category,
            status=status,
            limit=limit,
            offset=offset,
        )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [item_payload(item) for item in items],
        }

    async def search_items(self, *, query: str, limit: int = 20) -> dict:
        hits = await call_store(self._store, "search", query, limit=limit)
        return {"count": len(hits), "results": [item_summary(item) for item in hits]}
