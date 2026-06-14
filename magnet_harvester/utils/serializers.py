"""
MagnetItem serializers — API response formatting.
"""
from __future__ import annotations

from magnet_harvester.models import MagnetItem


def item_summary(item) -> dict:
    """Short form for list views — truncated hash."""
    return {
        "hash": item.hash[:16],
        "name": item.name,
        "category": item.category,
        "status": item.status.value,
    }


def item_payload(item: MagnetItem) -> dict:
    """Full form for detail views — complete model dump."""
    data = item.model_dump()
    data["status"] = item.status.value
    return data
