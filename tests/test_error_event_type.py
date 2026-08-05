"""
P2-13: 错误事件类型测试

缺陷: clear_all 使用 EventType.ERROR 广播 items_cleared，语义错误
修复: 添加 EventType.ITEMS_CLEARED 并使用它
"""

from magnet_harvester.bus import EventType


def test_items_cleared_event_type_exists():
    """验证 EventType.ITEMS_CLEARED 存在"""
    assert hasattr(EventType, "ITEMS_CLEARED")
    assert EventType.ITEMS_CLEARED.value == "items_cleared"


def test_as_dict_strips_data_type_key():
    """data 中任何 type 键不得覆盖事件类型。"""
    from magnet_harvester.bus import Event, EventType

    ev = Event(EventType.CRAWL_ERROR, {"type": "error", "msg": "爬取超时"})
    d = ev.as_dict()
    assert d["type"] == "crawl_error"  # as_dict 剥离 data 中的 type 键
    assert d["msg"] == "爬取超时"
