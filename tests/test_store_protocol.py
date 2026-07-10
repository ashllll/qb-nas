"""
测试 ItemStore 协议 — 验证 InMemoryItemStore 和 FakeStore 都符合协议
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import MagnetItem
from magnet_harvester.store import (
    InMemoryItemStore,
    ItemStore,
    SQLiteItemStore,
    StoreStats,
)


def _make_item(hash_key: str, name: str = "Test") -> MagnetItem:
    return MagnetItem(
        hash=hash_key,
        name=name,
        magnet=f"magnet:?xt=urn:btih:{hash_key}",
    )


def test_protocol_is_defined():
    """确保 ItemStore 协议存在且可导入"""
    assert ItemStore is not None
    assert hasattr(ItemStore, "add")
    assert hasattr(ItemStore, "get")
    assert hasattr(ItemStore, "update")
    assert hasattr(ItemStore, "remove")
    assert hasattr(ItemStore, "list")
    assert hasattr(ItemStore, "search")


def test_async_protocol_rejects_runtime_member_name_checks():
    """Runtime Protocol checks cannot prove that same-named methods are async."""
    with pytest.raises(TypeError):
        isinstance(InMemoryItemStore(), ItemStore)


# ── 行为测试（在协议上运行，任何实现都应通过） ──


def run_store_tests(store_factory):
    """针对 ItemStore 协议的通用测试套件"""
    store = store_factory()

    # add + get
    item = _make_item("AAAA")
    assert store.add(item) is True
    assert store.get("AAAA") is not None
    assert store.get("AAAA").hash == "AAAA"

    # dedup
    assert store.add(item) is False

    # update
    assert store.update("AAAA", category="电影") is True
    assert store.get("AAAA").category == "电影"

    # update non-existent
    assert store.update("ZZZZ", category="其他") is False

    # remove
    item2 = _make_item("BBBB")
    store.add(item2)
    assert store.remove("BBBB") is True
    assert store.get("BBBB") is None

    # remove non-existent
    assert store.remove("ZZZZ") is False

    # count
    assert store.count == 1  # only AAAA remains

    # search
    item3 = _make_item("CCCC", name="Avengers Movie")
    store.add(item3)
    results = store.search("avengers")
    assert len(results) == 1

    # list by category
    assert store.update("CCCC", category="电影")
    results = store.list(category="电影")
    assert len(results) >= 1

    # list by status
    results = store.list(status="pending")
    assert len(results) >= 1

    # get_pending (has category but status=pending)
    pending = store.get_pending()
    assert len(pending) >= 1

    # get_hashes_by_prefix
    hashes = store.get_hashes_by_prefix("AA")
    assert "AAAA" in hashes

    # stats
    stats = store.stats()
    assert stats.total >= 1
    assert isinstance(stats, StoreStats)

    # add_batch
    batch = [_make_item("DDDD"), _make_item("EEEE")]
    assert store.add_batch(batch) == 2
    assert store.add_batch(batch) == 0  # all duplicates

    # clear
    store.clear()
    assert store.count == 0

    print(f"  All protocol tests passed for {type(store_factory()).__name__}")


def test_inmemory_behaviors():
    run_store_tests(InMemoryItemStore)


def test_search_nonpositive_limit_is_consistent_across_adapters(tmp_path):
    def seed(store):
        store.add(_make_item("AAAA", name="Matrix A"))
        store.add(_make_item("BBBB", name="Matrix B"))
        return store

    stores = [
        seed(InMemoryItemStore()),
        seed(SQLiteItemStore(tmp_path / "search.db")),
    ]

    for store in stores:
        assert store.search("matrix", limit=0) == []
        assert store.search("matrix", limit=-1) == []


def test_count_and_page_boundary_values_are_consistent_across_adapters(tmp_path):
    def seed(store):
        store.add(_make_item("BBBB", name="Bravo"))
        store.add(_make_item("AAAA", name="Alpha"))
        return store

    stores = [
        seed(InMemoryItemStore()),
        seed(SQLiteItemStore(tmp_path / "page.db")),
    ]

    for store in stores:
        total, items = store.count_and_page(limit=0)
        assert total == 2
        assert items == []

        total, items = store.count_and_page(limit=-1)
        assert total == 2
        assert items == []

        total, items = store.count_and_page(limit=1, offset=-1)
        assert total == 2
        assert [item.name for item in items] == ["Alpha"]


def test_list_uses_limited_top_n_selection(monkeypatch):
    """小 limit 查询不应为了取前 N 条而全量排序。"""
    import magnet_harvester.store as store_module

    calls = []

    def fake_nsmallest(limit, items, key):
        calls.append(limit)
        return sorted(list(items), key=key)[:limit]

    monkeypatch.setattr(store_module.heapq, "nsmallest", fake_nsmallest)

    store = InMemoryItemStore()
    store.add(_make_item("CCCC", name="Charlie"))
    store.add(_make_item("AAAA", name="Alpha"))
    store.add(_make_item("BBBB", name="Bravo"))

    results = store.list(limit=2)

    assert [item.name for item in results] == ["Alpha", "Bravo"]
    assert calls == [2]


def test_add_batch_does_not_partially_commit_when_batch_is_invalid():
    store = InMemoryItemStore()

    try:
        store.add_batch([_make_item("AAAA"), object()])
    except AttributeError:
        pass
    else:
        raise AssertionError("invalid batch item should fail before commit")

    assert store.get("AAAA") is None
    assert store.count == 0


def test_fakestore_behaviors():
    from magnet_harvester.store import FakeStore

    run_store_tests(FakeStore)


if __name__ == "__main__":
    test_protocol_is_defined()
    test_async_protocol_rejects_runtime_member_name_checks()
    test_inmemory_behaviors()
    test_fakestore_behaviors()
    print("=== ItemStore Protocol tests passed! ===")
