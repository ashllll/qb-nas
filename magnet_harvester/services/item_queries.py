"""Read-only Magnet item query helpers."""

from __future__ import annotations

from magnet_harvester.store import ItemStore
from magnet_harvester.utils.serializers import item_payload, item_summary


class ItemQueryExecutor:
    """Formats read-only Magnet item queries behind a small interface."""

    def __init__(self, store: ItemStore):
        self._store = store

    def get_stats(self) -> dict:
        stats = self._store.stats()
        return {
            "total": stats.total,
            "by_category": stats.by_category,
            "by_status": stats.by_status,
        }

    def list_items(
        self,
        *,
        category: str | None = None,
        status: str = "all",
        limit: int = 20,
    ) -> dict:
        items = self._store.list(category=category, status=status, limit=limit)
        return {"count": len(items), "items": [item_summary(item) for item in items]}

    def page_items(
        self,
        *,
        category: str | None = None,
        status: str = "all",
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        items = self._store.list(category=category, status=status, limit=10000)
        total = len(items)
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [item_payload(item) for item in items[offset : offset + limit]],
        }

    def search_items(self, *, query: str, limit: int = 20) -> dict:
        hits = self._store.search(query)
        return {"count": len(hits), "results": [item_summary(item) for item in hits[:limit]]}
