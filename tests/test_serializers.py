"""
Test serializers — MagnetItem to API response dicts.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.utils.serializers import _item_summary, _item_payload


def test_item_summary_returns_truncated_hash_and_basic_fields():
    item = MagnetItem(
        hash="0123456789ABCDEF0123456789ABCDEF01234567",
        name="Test Movie 2024",
        magnet="magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
        category="电影",
        status=TaskStatus.pending,
    )

    result = _item_summary(item)

    assert result == {
        "hash": "0123456789ABCDEF",
        "name": "Test Movie 2024",
        "category": "电影",
        "status": "pending",
    }


def test_item_payload_returns_full_model_dump_with_status_value():
    item = MagnetItem(
        hash="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        name="Test",
        magnet="magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        category="电视剧",
        status=TaskStatus.downloading,
        progress=42.0,
    )

    result = _item_payload(item)

    assert result["hash"] == "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert result["name"] == "Test"
    assert result["category"] == "电视剧"
    assert result["status"] == "downloading"
    assert result["progress"] == 42.0
    assert "magnet" in result


if __name__ == "__main__":
    test_item_summary_returns_truncated_hash_and_basic_fields()
    test_item_payload_returns_full_model_dump_with_status_value()
    print("=== serializer tests passed! ===")
