"""后端一致性：内存与 SQLite 两个 ItemStore 后端必须语义相同。

已确证的差异点：
1. get_hashes_by_prefix —— 内存两侧 lower()，SQLite 走裸 LIKE；实测两者一致
   （LIKE 对 ASCII 默认不敏感 + hash 入库已 upper()），本文件把该一致性固定下来。
2. list / count_and_page 排序 —— 内存用 name.lower()，SQLite 用 ORDER BY name
   （BINARY，大小写敏感），同一批数据两个后端返回顺序不同。见下方排序测试。

manually_reclassify（services/user_actions.py）以用户输入前缀查找 hash，
/api/items 走 count_and_page，因此两处差异都是用户可见的。
"""

import os
import tempfile

import pytest

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import InMemoryItemStore, SQLiteItemStore

# 名称刻意混合大小写：BINARY 排序把大写排在小写之前，因此能暴露排序键差异
MIXED_CASE_NAMES = ["zebra item", "Apple item", "banana item", "Cherry item"]
# 完全相同名称、不同 hash：用于验证排序稳定（分页不重不漏）
DUPLICATE_NAMES = ["same name"] * 3


def _make_item(hash_val: str, name: str) -> MagnetItem:
    return MagnetItem(
        hash=hash_val,
        name=name,
        magnet=f"magnet:?xt=urn:btih:{hash_val}",
        status=TaskStatus.pending,
    )


def _item_hash(index: int) -> str:
    """生成 40 位十六进制 hash。首位按 index 区分（'B'/'C'/…），
    使前缀查询可唯一定位；其余位零填充。"""
    return f"{chr(ord('B') + index)}{'0' * 39}"


def _seed(param: str, names: list[str], db_path: str):
    """两个后端装入完全相同的一批条目。"""
    store = InMemoryItemStore() if param == "memory" else SQLiteItemStore(db_path)
    for index, name in enumerate(names):
        store.add(_make_item(_item_hash(index), name))
    return store


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    """两种后端各跑一遍，确保语义一致。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    try:
        backend = _seed(request.param, MIXED_CASE_NAMES, db_path)
        backend.backend_kind = request.param  # 便于断言失败时定位后端
        yield backend
    finally:
        try:
            os.unlink(db_path)
        except (PermissionError, FileNotFoundError):
            pass


def test_prefix_lookup_is_case_insensitive(store):
    """大写、小写前缀都必须命中同一条目；不匹配的前缀返回空。"""
    target = _item_hash(1)  # 首位 'C'
    for prefix in ("C", "c"):
        assert store.get_hashes_by_prefix(prefix) == [target], (
            f"前缀 {prefix!r} 未命中，后端行为不一致"
        )
    assert store.get_hashes_by_prefix("Z") == []


def test_prefix_lookup_rejects_non_matching(store):
    assert store.get_hashes_by_prefix("ZZZZZZZZ") == []


def test_prefix_lookup_escapes_like_wildcards(store):
    """前缀中的 LIKE 通配符必须被转义，不得当成模式匹配。"""
    assert store.get_hashes_by_prefix("%") == []
    assert store.get_hashes_by_prefix("_") == []


def test_list_orders_case_insensitively(store):
    """list() 必须按大小写不敏感的名称排序（与内存后端的 name.lower() 一致）。"""
    names = [item.name for item in store.list(limit=50)]
    expected = sorted(MIXED_CASE_NAMES, key=str.lower)
    assert names == expected, f"{store.backend_kind} 后端排序与预期不一致: {names} != {expected}"


def test_count_and_page_orders_case_insensitively(store):
    """count_and_page() 是 /api/items 的路径，排序必须与 list() 一致。"""
    _, items = store.count_and_page(limit=50, offset=0)
    names = [item.name for item in items]
    expected = sorted(MIXED_CASE_NAMES, key=str.lower)
    assert names == expected, (
        f"{store.backend_kind} 后端分页排序与预期不一致: {names} != {expected}"
    )


@pytest.mark.parametrize("param", ["memory", "sqlite"])
def test_pagination_is_stable_with_identical_names(param):
    """名称完全相同时排序必须稳定，否则分页会重复或漏项。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    try:
        backend = _seed(param, DUPLICATE_NAMES, db_path)
        _, first = backend.count_and_page(limit=2, offset=0)
        _, second = backend.count_and_page(limit=2, offset=2)
        seen = [item.hash for item in first] + [item.hash for item in second]
        assert len(seen) == 3, f"{param}: 分页结果数量不对 {len(seen)}"
        assert len(set(seen)) == 3, f"{param}: 分页出现重复项 {seen}"
    finally:
        try:
            os.unlink(db_path)
        except (PermissionError, FileNotFoundError):
            pass
