"""
P2-24: Agent 工具路径测试 (UserActionExecutor)
"""

import pytest
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.bus import MessageBus
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
    """验证 manually_reclassify 正确更新 category 和 save_path"""
    store = InMemoryItemStore()
    bus = MessageBus()
    async_store = AsyncItemStore(store)
    transitions = MagnetItemTransitions(store=async_store, bus=bus)
    executor = UserActionExecutor(
        store=async_store,
        pipeline=None,
        task_manager=None,
        transitions=transitions,
    )

    store.add(make_item("abc123def"))

    result = await executor.manually_reclassify("abc123de", "电影")
    assert result["status"] == "ok"
    assert result["new_category"] == "电影"

    item = store.get("abc123def")
    assert item.category == "电影"
    assert item.save_path == "其他"  # 保留原有 save_path，不因手动分类而清空
