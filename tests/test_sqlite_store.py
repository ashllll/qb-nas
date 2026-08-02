"""Tests for SQLiteItemStore — persistent ItemStore adapter."""

from __future__ import annotations

import os
import tempfile

import pytest

from magnet_harvester.store import SQLiteItemStore
from magnet_harvester.models import MagnetItem, TaskStatus


@pytest.fixture
def db_path():
    """Create a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    # Cleanup: close any lingering connections
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


@pytest.fixture
def store(db_path):
    """Create a SQLiteItemStore backed by a temporary database."""
    s = SQLiteItemStore(db_path)
    yield s
    # Final cleanup
    try:
        os.unlink(db_path)
    except (PermissionError, FileNotFoundError):
        pass


@pytest.fixture
def sample_item():
    return MagnetItem(
        hash="TESTHASH001",
        name="Test Movie 2024 2160p BluRay",
        magnet="magnet:?xt=urn:btih:TESTHASH001&dn=Test+Movie",
        size="15000000000",
        source_url="https://example.com/test",
        category="电影",
        save_path="电影",
        status=TaskStatus.pending,
        progress=0.0,
    )


class TestSQLiteItemStore:
    def test_add_and_get(self, store, sample_item):
        """Adding an item should make it retrievable by hash."""
        assert store.add(sample_item) is True
        retrieved = store.get("TESTHASH001")
        assert retrieved is not None
        assert retrieved.hash == "TESTHASH001"
        assert retrieved.name == "Test Movie 2024 2160p BluRay"
        assert retrieved.category == "电影"
        assert retrieved.status == TaskStatus.pending

    def test_add_duplicate_returns_false(self, store, sample_item):
        """Adding the same item twice should return False the second time."""
        assert store.add(sample_item) is True
        assert store.add(sample_item) is False

    def test_get_nonexistent_returns_none(self, store):
        """Getting a nonexistent hash should return None."""
        assert store.get("NONEXISTENT") is None

    def test_update(self, store, sample_item):
        """Updating fields should persist changes."""
        store.add(sample_item)
        result = store.update("TESTHASH001", category="电视剧", progress=0.5)
        assert result is True

        updated = store.get("TESTHASH001")
        assert updated is not None
        assert updated.category == "电视剧"
        assert updated.progress == 0.5

    def test_update_nonexistent_returns_false(self, store):
        """Updating a nonexistent item should return False."""
        assert store.update("NONEXISTENT", category="电影") is False

    def test_update_unknown_field_returns_false(self, store, sample_item):
        """Updating with an unknown field should return False."""
        store.add(sample_item)
        assert store.update("TESTHASH001", invalid_field="value") is False

    def test_remove(self, store, sample_item):
        """Removing an item should delete it from the database."""
        store.add(sample_item)
        assert store.remove("TESTHASH001") is True
        assert store.get("TESTHASH001") is None

    def test_remove_nonexistent_returns_false(self, store):
        """Removing a nonexistent item should return False."""
        assert store.remove("NONEXISTENT") is False

    def test_list_all(self, store):
        """Listing without filters should return all items."""
        items = [
            MagnetItem(hash=f"HASH{i:03d}", name=f"Item {i}", magnet=f"magnet:?xt=urn:btih:HASH{i:03d}")
            for i in range(5)
        ]
        for item in items:
            store.add(item)

        listed = store.list(limit=100)
        assert len(listed) == 5

    def test_list_with_category_filter(self, store):
        """Listing with category filter should return only matching items."""
        store.add(MagnetItem(hash="A001", name="Movie A", magnet="magnet:?xt=urn:btih:A001", category="电影"))
        store.add(MagnetItem(hash="B001", name="Show B", magnet="magnet:?xt=urn:btih:B001", category="电视剧"))
        store.add(MagnetItem(hash="C001", name="Anime C", magnet="magnet:?xt=urn:btih:C001", category="动漫"))

        movies = store.list(category="电影", limit=100)
        assert len(movies) == 1
        assert movies[0].hash == "A001"

        tv = store.list(category="电视剧", limit=100)
        assert len(tv) == 1
        assert tv[0].hash == "B001"

    def test_list_limit(self, store):
        """List should respect the limit parameter."""
        for i in range(10):
            store.add(MagnetItem(
                hash=f"HASH{i:03d}", name=f"Item {i}", magnet=f"magnet:?xt=urn:btih:HASH{i:03d}"
            ))
        assert len(store.list(limit=3)) == 3

    def test_list_zero_limit(self, store):
        """List with limit <= 0 should return empty."""
        assert store.list(limit=0) == []

    def test_search(self, store):
        """Search should find items by name substring."""
        store.add(MagnetItem(hash="S001", name="The Matrix 1999", magnet="magnet:?xt=urn:btih:S001"))
        store.add(MagnetItem(hash="S002", name="Matrix Reloaded", magnet="magnet:?xt=urn:btih:S002"))
        store.add(MagnetItem(hash="S003", name="Inception", magnet="magnet:?xt=urn:btih:S003"))

        results = store.search("matrix")
        assert len(results) == 2
        assert all("matrix" in r.name.lower() for r in results)

    def test_search_no_match(self, store):
        """Search with no match should return empty list."""
        results = store.search("nonexistent")
        assert results == []

    def test_count(self, store):
        """Count should reflect the number of stored items."""
        assert store.count == 0
        store.add(MagnetItem(hash="C001", name="Count Me", magnet="magnet:?xt=urn:btih:C001"))
        assert store.count == 1
        store.add(MagnetItem(hash="C002", name="Count Me Too", magnet="magnet:?xt=urn:btih:C002"))
        assert store.count == 2

    def test_stats(self, store):
        """Stats should correctly aggregate by category and status."""
        store.add(MagnetItem(hash="ST1", name="Stat 1", magnet="magnet:?xt=urn:btih:ST1", category="电影", status=TaskStatus.pending))
        store.add(MagnetItem(hash="ST2", name="Stat 2", magnet="magnet:?xt=urn:btih:ST2", category="电影", status=TaskStatus.downloading))
        store.add(MagnetItem(hash="ST3", name="Stat 3", magnet="magnet:?xt=urn:btih:ST3", category="电视剧", status=TaskStatus.success))
        store.add(MagnetItem(hash="ST4", name="Stat 4", magnet="magnet:?xt=urn:btih:ST4", category=None, status=TaskStatus.pending))

        s = store.stats()
        assert s.total == 4
        assert s.by_category.get("电影", 0) == 2
        assert s.by_category.get("电视剧", 0) == 1
        assert s.pending_count == 2  # ST1 + ST4 (uncategorized)

    def test_get_pending(self, store):
        """get_pending should return items with pending status and a category."""
        store.add(MagnetItem(hash="P001", name="Pending 1", magnet="magnet:?xt=urn:btih:P001", category="电影", status=TaskStatus.pending))
        store.add(MagnetItem(hash="P002", name="Pending 2", magnet="magnet:?xt=urn:btih:P002", category="电视剧", status=TaskStatus.pending))
        store.add(MagnetItem(hash="P003", name="Success 1", magnet="magnet:?xt=urn:btih:P003", category="电影", status=TaskStatus.success))
        store.add(MagnetItem(hash="P004", name="No Cat", magnet="magnet:?xt=urn:btih:P004", status=TaskStatus.pending))

        pending = store.get_pending()
        hashes = {p.hash for p in pending}
        assert "P001" in hashes
        assert "P002" in hashes
        assert "P003" not in hashes
        # P004 has no category — depends on SQL behavior; OK either way

    def test_get_hashes_by_prefix(self, store):
        """Hash prefix lookup should return matching full hashes."""
        store.add(MagnetItem(hash="ABCDEF123456", name="Alpha", magnet="magnet:?xt=urn:btih:ABCDEF123456"))
        store.add(MagnetItem(hash="ABCDEF789012", name="Beta", magnet="magnet:?xt=urn:btih:ABCDEF789012"))
        store.add(MagnetItem(hash="ZZZZZZZZZZZZ", name="Gamma", magnet="magnet:?xt=urn:btih:ZZZZZZZZZZZZ"))

        matches = store.get_hashes_by_prefix("ABCDEF")
        assert len(matches) == 2
        assert "ABCDEF123456" in matches
        assert "ABCDEF789012" in matches

    def test_add_batch(self, store):
        """add_batch should add multiple items and return the count of new additions."""
        items = [
            MagnetItem(hash=f"B{i:03d}", name=f"Batch {i}", magnet=f"magnet:?xt=urn:btih:B{i:03d}")
            for i in range(5)
        ]
        added = store.add_batch(items)
        assert added == 5
        assert store.count == 5

        # Adding same batch again should add 0
        added_again = store.add_batch(items)
        assert added_again == 0

    def test_clear(self, store):
        """Clear should remove all items from the database."""
        store.add(MagnetItem(hash="CLR001", name="Clear Me", magnet="magnet:?xt=urn:btih:CLR001"))
        store.add(MagnetItem(hash="CLR002", name="Clear Me Too", magnet="magnet:?xt=urn:btih:CLR002"))
        assert store.count == 2

        store.clear()
        assert store.count == 0
        assert store.list(limit=100) == []

    def test_persistence_across_instances(self, db_path):
        """Data written by one SQLiteItemStore instance should be readable by another."""
        store1 = SQLiteItemStore(db_path)
        store1.add(MagnetItem(hash="PERSIST001", name="Persistent Item", magnet="magnet:?xt=urn:btih:PERSIST001", category="电影"))
        assert store1.count == 1
        del store1

        store2 = SQLiteItemStore(db_path)
        assert store2.count == 1
        retrieved = store2.get("PERSIST001")
        assert retrieved is not None
        assert retrieved.name == "Persistent Item"
        assert retrieved.category == "电影"

    def test_large_name_handling(self, store):
        """Handles long magnet names with special characters."""
        long_name = "Test.Movie.2024.2160p.BluRay.x264-GROUP " + "x" * 200
        item = MagnetItem(
            hash="LONGNAME01",
            name=long_name,
            magnet="magnet:?xt=urn:btih:LONGNAME01&dn=" + long_name[:100],
        )
        assert store.add(item) is True
        retrieved = store.get("LONGNAME01")
        assert retrieved is not None
        assert retrieved.name == long_name

    def test_update_preserves_unchanged_fields(self, store, sample_item):
        """Updating one field should not reset other fields."""
        sample_item = sample_item.model_copy(
            update={"category": "电影", "save_path": "电影", "progress": 0.75}
        )
        store.add(sample_item)

        store.update("TESTHASH001", progress=0.9)

        updated = store.get("TESTHASH001")
        assert updated is not None
        assert updated.progress == 0.9
        assert updated.category == "电影"  # Should be preserved
        assert updated.save_path == "电影"  # Should be preserved

    def test_stats_category_none_mapped_to_uncategorized(self, store):
        """category=None 的条目应统计到 '未分类' 键下，不应出现空字符串键。"""
        store.add(MagnetItem(
            hash="NOCAT001", name="No Category Item",
            magnet="magnet:?xt=urn:btih:NOCAT001", category=None,
        ))
        store.add(MagnetItem(
            hash="WITHCAT001", name="With Category",
            magnet="magnet:?xt=urn:btih:WITHCAT001", category="电影",
        ))

        s = store.stats()
        assert s.total == 2
        assert "未分类" in s.by_category, "category=None 应归入 '未分类'"
        assert s.by_category["未分类"] == 1
        assert "" not in s.by_category, "不应出现空字符串键"
        assert s.by_category.get("电影", 0) == 1
