"""
P0-1: Store 键遍历崩溃测试

缺陷: get_hashes_by_prefix 直接迭代 self._items 的 keys，
      并发修改时触发 RuntimeError: dictionary changed size during iteration
修复: 使用 list(self._items.keys()) 创建快照后再迭代
"""

import asyncio
import pytest
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.models import MagnetItem, TaskStatus


def make_item(hash_val: str) -> MagnetItem:
    return MagnetItem(
        hash=hash_val,
        name=f"Test {hash_val}",
        magnet=f"magnet:?xt=urn:btih:{hash_val}",
        status=TaskStatus.pending,
    )


@pytest.mark.asyncio
async def test_get_hashes_by_prefix_during_concurrent_modification():
    """模拟并发 add/remove 时 get_hashes_by_prefix 不崩溃"""
    store = InMemoryItemStore()

    # 预填充一些数据
    for i in range(100):
        store.add(make_item(f"abc{i:03d}"))

    async def reader():
        for _ in range(500):
            store.get_hashes_by_prefix("abc")
            await asyncio.sleep(0)

    async def writer():
        for i in range(500):
            store.add(make_item(f"xyz{i:03d}"))
            store.remove(f"abc{i % 100:03d}")
            await asyncio.sleep(0)

    await asyncio.gather(reader(), writer())
    # 如果没有崩溃，测试就通过了


def test_get_hashes_by_prefix_basic():
    """基本功能测试"""
    store = InMemoryItemStore()
    store.add(make_item("abcdef123"))
    store.add(make_item("abcxyz789"))
    store.add(make_item("bbb000000"))

    result = store.get_hashes_by_prefix("abc")
    assert len(result) == 2
    assert "abcdef123" in result
    assert "abcxyz789" in result

    result = store.get_hashes_by_prefix("bbb")
    assert result == ["bbb000000"]

    result = store.get_hashes_by_prefix("zzz")
    assert result == []
