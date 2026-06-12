"""
P2-16: 时间戳字段测试

缺陷: MagnetItem 没有 created_at 或 updated_at 字段，无法按时间排序、判断 item 年龄
修复: 添加 created_at 和 updated_at 字段，default_factory=datetime.now
"""
from datetime import datetime
from magnet_harvester.models import MagnetItem


def test_magnet_item_has_timestamp_fields():
    """验证 MagnetItem 有 created_at 和 updated_at 字段"""
    item = MagnetItem(
        hash="abc123",
        name="Test",
        magnet="magnet:?xt=urn:btih:abc123",
    )
    assert isinstance(item.created_at, datetime)
    assert isinstance(item.updated_at, datetime)


def test_magnet_item_timestamps_are_set_on_creation():
    """验证创建时自动设置时间戳"""
    before = datetime.now()
    item = MagnetItem(
        hash="abc123",
        name="Test",
        magnet="magnet:?xt=urn:btih:abc123",
    )
    after = datetime.now()

    assert before <= item.created_at <= after
    assert before <= item.updated_at <= after


def test_magnet_item_model_dump_includes_timestamps():
    """验证 model_dump 包含时间戳字段"""
    item = MagnetItem(
        hash="abc123",
        name="Test",
        magnet="magnet:?xt=urn:btih:abc123",
    )
    data = item.model_dump()
    assert "created_at" in data
    assert "updated_at" in data
