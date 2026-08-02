"""
P2-24: Agent 工具路径测试 (UserActionExecutor)
"""

import pytest
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions
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
    discovery = DiscoveryTransitions(store=async_store, bus=bus)
    classification = ClassificationTransitions(store=async_store, bus=bus)
    executor = UserActionExecutor(
        store=async_store,
        pipeline=None,
        task_manager=None,
        discovery=discovery,
        classification=classification,
    )

    store.add(make_item("abc123def"))

    result = await executor.manually_reclassify("abc123de", "电影")
    assert result["status"] == "ok"
    assert result["new_category"] == "电影"

    item = store.get("abc123def")
    assert item.category == "电影"
    assert item.save_path == "其他"  # 保留原有 save_path，不因手动分类而清空


@pytest.mark.asyncio
async def test_manual_reclassify_does_not_overwrite_concurrent_save_path():
    class ConcurrentPathStore(InMemoryItemStore):
        def update(self, hash_key: str, **fields) -> bool:
            InMemoryItemStore.update(self, hash_key, save_path="/concurrent")
            return InMemoryItemStore.update(self, hash_key, **fields)

        def update_if_status(self, hash_key, expected_statuses, **fields) -> bool:
            InMemoryItemStore.update(self, hash_key, save_path="/concurrent")
            return InMemoryItemStore.update_if_status(
                self,
                hash_key,
                expected_statuses,
                **fields,
            )

    store = ConcurrentPathStore()
    bus = MessageBus()
    transitions = ClassificationTransitions(store=AsyncItemStore(store), bus=bus)
    store.add(make_item("concurrent-path"))

    assert await transitions.manually_classified("concurrent-path", "电影") is True
    current = await AsyncItemStore(store).get("concurrent-path")
    assert current is not None
    assert current.category == "电影"
    assert current.save_path == "/concurrent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        TaskStatus.classifying,
        TaskStatus.adding,
        TaskStatus.queued,
        TaskStatus.downloading,
        TaskStatus.success,
    ],
)
async def test_manual_reclassify_rejects_non_editable_states(status):
    store = InMemoryItemStore()
    bus = MessageBus()
    transitions = ClassificationTransitions(store=AsyncItemStore(store), bus=bus)
    item = make_item(f"blocked-{status.value}").model_copy(update={"status": status})
    store.add(item)

    assert await transitions.manually_classified(item.hash, "电影") is False
    assert store.get(item.hash).category == "其他"
