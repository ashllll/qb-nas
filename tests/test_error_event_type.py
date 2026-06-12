"""
P2-13: 错误事件类型测试

缺陷: clear_all 使用 EventType.ERROR 广播 items_cleared，语义错误
修复: 添加 EventType.ITEMS_CLEARED 并使用它
"""
import pytest
from magnet_harvester.bus import EventType


def test_items_cleared_event_type_exists():
    """验证 EventType.ITEMS_CLEARED 存在"""
    assert hasattr(EventType, "ITEMS_CLEARED")
    assert EventType.ITEMS_CLEARED.value == "items_cleared"
