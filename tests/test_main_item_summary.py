"""测试 serializers 的 API 序列化格式。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.utils.serializers import _item_payload, _item_summary
from magnet_harvester.models import MagnetItem, TaskStatus


def test_item_summary_uses_status_value():
    item = MagnetItem(
        hash="ABCDEF1234567890",
        name="Example",
        magnet="magnet:?xt=urn:btih:ABCDEF1234567890",
        category="电影",
        status=TaskStatus.pending,
    )

    result = _item_summary(item)

    assert result == {
        "hash": "ABCDEF1234567890",
        "name": "Example",
        "category": "电影",
        "status": "pending",
    }


def test_item_payload_uses_status_value():
    item = MagnetItem(
        hash="ABCDEF1234567890",
        name="Example",
        magnet="magnet:?xt=urn:btih:ABCDEF1234567890",
        category="电影",
        status=TaskStatus.downloading,
    )

    result = _item_payload(item)

    assert result["status"] == "downloading"


if __name__ == "__main__":
    test_item_summary_uses_status_value()
    test_item_payload_uses_status_value()
    print("=== main._item_summary tests passed! ===")
