"""后端一致性：get_hashes_by_prefix 在内存与 SQLite 后端必须语义相同。

背景：manually_reclassify（services/user_actions.py）以用户输入的前缀查找 hash。
内存实现两侧 lower() → 大小写不敏感；SQLite 实现用裸 LIKE，对 ASCII 不敏感，
但实际存的是大写 hash（magnet 解析会 upper()），导致同一前缀在两个后端结果不同。
"""

import os
import tempfile

import pytest

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import InMemoryItemStore, SQLiteItemStore

HASH = "ABCDEF1234567890ABCDEF1234567890ABCDEF12"


def _make_item(hash_val: str) -> MagnetItem:
    return MagnetItem(
        hash=hash_val,
        name=f"Test {hash_val}",
        magnet=f"magnet:?xt=urn:btih:{hash_val}",
        status=TaskStatus.pending,
    )


def _in_memory_store() -> InMemoryItemStore:
    store = InMemoryItemStore()
    store.add(_make_item(HASH))
    return store


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    """两种后端各跑一遍，确保语义一致。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    try:
        if request.param == "memory":
            yield _in_memory_store()
        else:
            instance = SQLiteItemStore(db_path)
            instance.add(_make_item(HASH))
            yield instance
    finally:
        try:
            os.unlink(db_path)
        except (PermissionError, FileNotFoundError):
            pass


def test_prefix_lookup_is_case_insensitive(store):
    """大写、小写、混合大小写前缀都必须命中同一条目。"""
    for prefix in (HASH[:12], HASH[:12].lower(), HASH[:6].lower() + HASH[6:12]):
        assert store.get_hashes_by_prefix(prefix) == [HASH], (
            f"前缀 {prefix!r} 未命中，后端行为不一致"
        )


def test_prefix_lookup_rejects_non_matching(store):
    assert store.get_hashes_by_prefix("ZZZZZZZZ") == []


def test_prefix_lookup_escapes_like_wildcards(store):
    """前缀中的 LIKE 通配符必须被转义，不得当成模式匹配。"""
    assert store.get_hashes_by_prefix("ABC%") == []
    assert store.get_hashes_by_prefix("ABC_") == []
