"""存储查询基线：索引、keyset 分页、批量读取与排序一致性。"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from magnet_harvester.bus import MessageBus
from magnet_harvester.models import SYNC_ACTIVE_STATUSES, MagnetItem, TaskStatus
from magnet_harvester.services import qbit_sync
from magnet_harvester.services.observability import ObservabilitySnapshot
from magnet_harvester.services.qbit_sync import QBitSyncLoop
from magnet_harvester.store import (
    _SYNC_PAGE_SQL,
    AsyncItemStore,
    InMemoryItemStore,
    SQLiteItemStore,
    StoreStats,
)


def _item(hash_key: str, name: str | None = None, status: TaskStatus = TaskStatus.queued):
    return MagnetItem(
        hash=hash_key,
        name=name or f"Name {hash_key}",
        magnet=f"magnet:?xt=urn:btih:{hash_key}",
        status=status,
    )


def _seed_10k(db_path):
    with sqlite3.connect(db_path) as db:
        rows = []
        for index in range(10000):
            status = "downloading" if index % 2 else "success"
            rows.append(
                (
                    f"H{index:040d}",
                    f"Name {index}",
                    f"magnet:?xt=urn:btih:H{index:040d}",
                    status,
                    "电影" if index % 3 else None,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                )
            )
        db.executemany(
            "INSERT INTO magnet_items(hash, name, magnet, status, category, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        db.commit()


def test_sqlite_has_query_indexes_and_sync_query_avoids_sort(tmp_path):
    db_path = tmp_path / "indexes.db"
    SQLiteItemStore(db_path)

    with sqlite3.connect(db_path) as db:
        indexes = {row[1] for row in db.execute("PRAGMA index_list('magnet_items')")}
        assert {
            "idx_magnet_items_status",
            "idx_magnet_items_category",
            "idx_magnet_items_created_at",
            "idx_magnet_items_name_nocase",
            "idx_magnet_items_sync",
        } <= indexes

        statuses = ", ".join(f"'{status}'" for status in SYNC_ACTIVE_STATUSES)
        sql = _SYNC_PAGE_SQL.format(where=f"status IN ({statuses}) AND hash > ?")
        plan = "\n".join(
            " ".join(str(column) for column in row)
            for row in db.execute(f"EXPLAIN QUERY PLAN {sql}", ["H000", 500])
        )

    assert "idx_magnet_items_sync" in plan
    assert "TEMP B-TREE" not in plan.upper()
    assert "SCAN magnet_items" not in plan


def test_sync_query_plan_stays_indexed_at_10k(tmp_path):
    db_path = tmp_path / "ten-thousand.db"
    SQLiteItemStore(db_path)
    _seed_10k(db_path)

    with sqlite3.connect(db_path) as db:
        statuses = ", ".join(f"'{status}'" for status in SYNC_ACTIVE_STATUSES)
        sql = _SYNC_PAGE_SQL.format(where=f"status IN ({statuses}) AND hash > ?")
        plan = "\n".join(
            " ".join(str(column) for column in row)
            for row in db.execute(f"EXPLAIN QUERY PLAN {sql}", ["H000", 500])
        )

    assert "idx_magnet_items_sync" in plan
    assert "TEMP B-TREE" not in plan.upper()
    assert "SCAN magnet_items" not in plan


def test_list_for_sync_pages_cover_tail_without_reordering(tmp_path):
    store_factories = [InMemoryItemStore, lambda: SQLiteItemStore(tmp_path / "pages.db")]
    for factory in store_factories:
        store = factory()
        for hash_key in ("AAAA", "BBBB", "CCCC", "DDDD", "EEEE"):
            store.add(_item(hash_key))

        first = store.list_for_sync(limit=2)
        second = store.list_for_sync(after_hash=first[-1].hash, limit=2)
        third = store.list_for_sync(after_hash=second[-1].hash, limit=2)
        exhausted = store.list_for_sync(after_hash=third[-1].hash, limit=2)

        assert [item.hash for item in first] == ["AAAA", "BBBB"]
        assert [item.hash for item in second] == ["CCCC", "DDDD"]
        assert [item.hash for item in third] == ["EEEE"]
        assert exhausted == []


def test_list_for_sync_only_returns_active_statuses(tmp_path):
    store_factories = [InMemoryItemStore, lambda: SQLiteItemStore(tmp_path / "active.db")]
    for factory in store_factories:
        store = factory()
        store.add(_item("ACTIVE", status=TaskStatus.downloading))
        store.add(_item("PENDING", status=TaskStatus.pending))
        store.add(_item("DONE", status=TaskStatus.success))
        assert [item.hash for item in store.list_for_sync()] == ["ACTIVE"]
        assert store.list_for_sync(limit=0) == []


def test_sync_loop_cursor_reaches_tail_over_three_rounds(monkeypatch):
    class FakeQbit:
        def __init__(self, snapshot):
            self.snapshot = snapshot

        async def poll_torrent_snapshot_with_removed(self):
            return dict(self.snapshot), set()

        def take_recently_removed(self):
            return set()

    store = InMemoryItemStore()
    hashes = ["H1", "H2", "H3", "H4", "H5"]
    for hash_key in hashes:
        store.add(_item(hash_key))
    snapshot = {hash_key.lower(): {"state": "downloading", "progress": 0.25} for hash_key in hashes}

    monkeypatch.setattr(qbit_sync, "_SYNC_PAGE_SIZE", 2)
    loop = QBitSyncLoop(
        qbit_client=FakeQbit(snapshot),
        store=AsyncItemStore(store),
        bus=MessageBus(),
    )

    async def run_rounds():
        for _ in range(3):
            await loop._sync_round(loop._qbit, loop._store)

    asyncio.run(run_rounds())

    assert loop._sync_cursor is None
    assert [store.get(hash_key).status for hash_key in hashes] == [TaskStatus.downloading] * 5


def test_removed_tail_item_survives_between_pages(monkeypatch):
    class PollingQbit:
        def __init__(self, rounds):
            self.rounds = list(rounds)

        async def poll_torrent_snapshot_with_removed(self):
            return self.rounds.pop(0)

        def take_recently_removed(self):
            return set()

    hashes = ["H1", "H2", "H3", "H4"]
    snapshot = {hash_key.lower(): {"state": "downloading", "progress": 0.25} for hash_key in hashes}
    tail_removed = {key: value for key, value in snapshot.items() if key not in {"h3"}}
    qbit = PollingQbit(
        [
            (snapshot, set()),
            (tail_removed, {"h3"}),
            (snapshot, set()),
        ]
    )

    store = InMemoryItemStore()
    for hash_key in hashes:
        store.add(_item(hash_key))

    monkeypatch.setattr(qbit_sync, "_SYNC_PAGE_SIZE", 2)
    loop = QBitSyncLoop(qbit_client=qbit, store=AsyncItemStore(store), bus=MessageBus())

    async def run_rounds():
        for _ in range(3):
            await loop._sync_round(loop._qbit, loop._store)

    asyncio.run(run_rounds())

    assert store.get("H3").status == TaskStatus.error
    assert store.get("H4").status == TaskStatus.downloading
    assert loop._removed_pending == set()


def test_get_many_uses_one_connection_and_ignores_missing(tmp_path):
    store = SQLiteItemStore(tmp_path / "many.db")
    for hash_key in ("AAAA", "BBBB", "CCCC"):
        store.add(_item(hash_key))

    calls = []
    original = store._connect

    def counting_connect():
        calls.append(1)
        return original()

    store._connect = counting_connect
    found = store.get_many(["AAAA", "BBBB", "MISSING"])
    assert set(found) == {"AAAA", "BBBB"}
    assert len(calls) == 1
    assert store.get_many([]) == {}


def test_update_returns_written_item_without_second_read(tmp_path):
    store = SQLiteItemStore(tmp_path / "update.db")
    store.add(_item("AAAA"))

    calls = []
    original = store._connect

    def counting_connect():
        calls.append(1)
        return original()

    store._connect = counting_connect
    updated = store.update("AAAA", category="电影")
    assert updated is not None and updated.category == "电影"
    assert len(calls) == 1

    stored = store.get("AAAA")
    assert stored is not None and stored.updated_at == updated.updated_at


def test_update_if_status_returns_item_and_rejects_mismatch():
    for store in (InMemoryItemStore(),):
        store.add(_item("AAAA", status=TaskStatus.pending))
        updated = store.update_if_status(
            "AAAA", {TaskStatus.pending}, status=TaskStatus.classifying
        )
        assert updated is not None and updated.status == TaskStatus.classifying
        assert store.update_if_status("AAAA", {TaskStatus.pending}, status=TaskStatus.error) is None


def test_list_sort_is_case_insensitive_across_adapters(tmp_path):
    store_factories = [InMemoryItemStore, lambda: SQLiteItemStore(tmp_path / "sort.db")]
    for factory in store_factories:
        store = factory()
        for hash_key, name in (("AAAA", "beta"), ("BBBB", "Alpha"), ("CCCC", "gamma")):
            store.add(_item(hash_key, name=name))
        assert [item.name for item in store.list(limit=10)] == ["Alpha", "beta", "gamma"]
        _, page = store.count_and_page(limit=10)
        assert [item.name for item in page] == ["Alpha", "beta", "gamma"]


def test_stats_total_does_not_need_a_separate_count(tmp_path):
    store = SQLiteItemStore(tmp_path / "stats.db")
    store.add(_item("AAAA", status=TaskStatus.downloading))
    store.add(_item("BBBB", status=TaskStatus.success))
    stats = store.stats()
    assert stats.total == 2 == store.count
    assert stats.total == sum(stats.by_status.values())


def test_system_status_uses_stats_total(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.count_calls = 0

        async def stats(self):
            return StoreStats(
                total=3,
                by_status={"downloading": 2, "success": 1},
            )

        @property
        def count(self):
            self.count_calls += 1
            raise AssertionError("system_status 不应再单独 count()")

    class FakeQbit:
        async def ping(self):
            return True

        def get_stats(self):
            return {}

    store = FakeStore()
    monkeypatch.setattr(
        "magnet_harvester.services.observability.settings",
        SimpleNamespace(check_disk_space=lambda: {"free_gb": 100}),
    )
    snapshot = ObservabilitySnapshot(store=store, qbit=FakeQbit())

    result = asyncio.run(snapshot.system_status())
    assert result["items_count"] == 3
    assert result["tracked_downloads"] == 2
    assert store.count_calls == 0
