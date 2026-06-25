"""
MagnetItem serializers — API response formatting.
"""

from __future__ import annotations

from magnet_harvester.models import MagnetItem


def item_summary(item) -> dict:
    """Short form for list views — truncated hash."""
    if item is None:
        return {"hash": "", "name": "", "category": "", "status": ""}
    return {
        "hash": item.hash[:16] if item.hash else "",
        "name": item.name or "",
        "category": item.category or "",
        "status": item.status.value if item.status else "",
    }


def item_payload(item: MagnetItem) -> dict:
    """Full form for detail views — complete model dump."""
    data = item.model_dump()
    data["status"] = item.status.value
    return data
