"""
P2-24: Agent 工具路径测试

缺陷: reclassify_item 将 save_path 直接设为分类名，而不是实际文件系统路径
验证: qbit_client 已自动处理非绝对路径的拼接，但 agent_tools 应使用空字符串让 qB 自动管理
"""
import pytest
from magnet_harvester.services.agent_tools import ToolExecutor
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.bus import MessageBus, EventType
from magnet_harvester.models import MagnetItem, TaskStatus


def make_item(hash_val: str) -> MagnetItem:
    return MagnetItem(
        hash=hash_val,
        name=f"Test {hash_val}",
        magnet=f"magnet:?xt=urn:btih:{hash_val}",
        status=TaskStatus.pending,
        category="其他",
        save_path="其他",
    )


@pytest.mark.asyncio
async def test_reclassify_item_updates_category_and_save_path():
    """验证 reclassify_item 正确更新 category 和 save_path"""
    store = InMemoryItemStore()
    bus = MessageBus()
    executor = ToolExecutor(store, None, bus)

    store.add(make_item("abc123def"))

    result = await executor.execute("reclassify_item", {"hash": "abc123de", "category": "电影"})
    assert result["status"] == "ok"
    assert result["new_category"] == "电影"

    item = store.get("abc123def")
    assert item.category == "电影"
    # save_path 应为空字符串，让 qB 自动管理路径
    assert item.save_path == ""
