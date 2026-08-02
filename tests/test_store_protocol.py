"""
测试 ItemStore 协议 — 验证 InMemoryItemStore 和 FakeStore 都符合协议
"""

import sys
import os
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import InMemoryItemStore, ItemStore, SQLiteItemStore, StoreStats


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
    assert hasattr(ItemStore, "update_if_status")
    assert hasattr(ItemStore, "remove")
    assert hasattr(ItemStore, "list")
    assert hasattr(ItemStore, "search")


def test_inmemory_conforms_to_protocol():
    """InMemoryItemStore 是 ItemStore 的实例"""
    store = InMemoryItemStore()
    assert isinstance(store, ItemStore)


def test_fake_store_conforms_to_protocol():
    """FakeStore 是 ItemStore 的实例"""
    from magnet_harvester.store import FakeStore

    store = FakeStore()
    assert isinstance(store, ItemStore)


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


def test_search_nonpositive_limit_is_consistent_across_adapters():
    def seed(store):
        store.add(_make_item("AAAA", name="Matrix A"))
        store.add(_make_item("BBBB", name="Matrix B"))
        return store

    with tempfile.NamedTemporaryFile(suffix=".db") as db:
        stores = [
            seed(InMemoryItemStore()),
            seed(SQLiteItemStore(db.name)),
        ]

        for store in stores:
            assert store.search("matrix", limit=0) == []
            assert store.search("matrix", limit=-1) == []


def test_count_and_page_boundary_values_are_consistent_across_adapters():
    def seed(store):
        store.add(_make_item("BBBB", name="Bravo"))
        store.add(_make_item("AAAA", name="Alpha"))
        return store

    with tempfile.NamedTemporaryFile(suffix=".db") as db:
        stores = [
            seed(InMemoryItemStore()),
            seed(SQLiteItemStore(db.name)),
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


def test_update_if_status_is_consistent_across_adapters():
    """状态比较与字段更新必须在 adapter 的同一次原子操作中完成。"""
    with tempfile.NamedTemporaryFile(suffix=".db") as db:
        stores = [InMemoryItemStore(), SQLiteItemStore(db.name)]

        for index, store in enumerate(stores):
            hash_key = f"CAS-{index}"
            store.add(_make_item(hash_key))

            assert store.update_if_status(
                hash_key,
                {TaskStatus.pending},
                status=TaskStatus.classifying,
            )
            assert not store.update_if_status(
                hash_key,
                {TaskStatus.pending},
                category="不应写入",
            )

            current = store.get(hash_key)
            assert current is not None
            assert current.status == TaskStatus.classifying
            assert current.category is None


def test_inmemory_update_if_status_holds_lock_for_read_and_write():
    """内存 adapter 的状态读取与对象替换必须处于同一临界区。"""

    class LockCheckingDict(dict):
        def __init__(self, lock, initial):
            super().__init__(initial)
            self._lock = lock

        def get(self, key, default=None):
            assert self._lock.locked(), "conditional read escaped the store lock"
            return super().get(key, default)

        def __setitem__(self, key, value):
            assert self._lock.locked(), "conditional write escaped the store lock"
            return super().__setitem__(key, value)

    store = InMemoryItemStore()
    store.add(_make_item("LOCKED-CAS"))
    store._items = LockCheckingDict(store._lock, store._items)

    assert store.update_if_status(
        "LOCKED-CAS",
        {TaskStatus.pending},
        status=TaskStatus.classifying,
    )


def test_sqlite_update_if_status_is_atomic_across_adapter_instances(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db") as db:
        first = SQLiteItemStore(db.name)
        second = SQLiteItemStore(db.name)
        first.add(_make_item("SHARED-CAS"))
        barrier = threading.Barrier(2)
        write_updated_fields = SQLiteItemStore._write_updated_fields

        def coordinated_write(cls, connection, current_hash, item, fields, expected_statuses=None):
            # 两个 adapter 都已读取 pending 快照后才允许执行带状态条件的 UPDATE。
            barrier.wait(timeout=2)
            return write_updated_fields(
                connection,
                current_hash,
                item,
                fields,
                expected_statuses,
            )

        monkeypatch.setattr(
            SQLiteItemStore,
            "_write_updated_fields",
            classmethod(coordinated_write),
        )

        def admit(store, category):
            return store.update_if_status(
                "SHARED-CAS",
                {TaskStatus.pending},
                status=TaskStatus.classifying,
                category=category,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            decisions = list(
                executor.map(
                    lambda args: admit(*args),
                    [(first, "电影"), (second, "电视剧")],
                )
            )

        assert sorted(decisions) == [False, True]
        current = first.get("SHARED-CAS")
        assert current is not None
        assert current.status == TaskStatus.classifying
        assert current.category in {"电影", "电视剧"}


def test_hash_is_immutable_across_adapters():
    """条目主键不能通过普通更新或状态条件更新移动。"""
    with tempfile.NamedTemporaryFile(suffix=".db") as db:
        stores = [InMemoryItemStore(), SQLiteItemStore(db.name)]

        for index, store in enumerate(stores):
            original_hash = f"IMMUTABLE-{index}"
            replacement_hash = f"REPLACED-{index}"
            store.add(_make_item(original_hash))

            assert store.update(original_hash, hash=replacement_hash) is False
            assert (
                store.update_if_status(
                    original_hash,
                    {TaskStatus.pending},
                    hash=replacement_hash,
                )
                is False
            )
            assert store.get(original_hash) is not None
            assert store.get(replacement_hash) is None


def test_inmemory_get_cannot_mutate_stored_item_outside_store_interface():
    store = InMemoryItemStore()
    store.add(_make_item("FROZEN"))

    retrieved = store.get("FROZEN")
    assert retrieved is not None
    with pytest.raises(ValidationError):
        retrieved.status = TaskStatus.success

    assert store.get("FROZEN").status == TaskStatus.pending


def test_sqlite_update_if_status_preserves_unrelated_concurrent_fields():
    """条件更新只写请求字段，不得用旧快照覆盖同状态下的无关修改。"""
    with tempfile.NamedTemporaryFile(suffix=".db") as db:
        store = SQLiteItemStore(db.name)
        store.add(_make_item("PARTIAL-CAS"))
        with sqlite3.connect(db.name) as connection:
            connection.execute("""
                CREATE TRIGGER update_category_before_save_path
                BEFORE UPDATE OF save_path ON magnet_items
                BEGIN
                    UPDATE magnet_items
                    SET category = '并发分类'
                    WHERE hash = OLD.hash;
                END
            """)

        assert store.update_if_status(
            "PARTIAL-CAS",
            {TaskStatus.pending},
            save_path="/downloads/movie",
        )

        current = store.get("PARTIAL-CAS")
        assert current is not None
        assert current.category == "并发分类"
        assert current.save_path == "/downloads/movie"


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
    test_inmemory_conforms_to_protocol()
    test_fake_store_conforms_to_protocol()
    test_inmemory_behaviors()
    test_fakestore_behaviors()
    print("=== ItemStore Protocol tests passed! ===")
