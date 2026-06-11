"""
TDD 循环 1: Pydantic 模型更新与存储一致性
验证 ItemStore.update() 通过 Pydantic 验证保持类型安全
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import InMemoryItemStore


def _make_item(hash_key: str, name: str = "Test") -> MagnetItem:
    return MagnetItem(
        hash=hash_key,
        name=name,
        magnet=f"magnet:?xt=urn:btih:{hash_key}",
    )


# ═══════════════════════════════════════════════════
# 示踪弹: update() 应拒绝非法 status 字符串
# ═══════════════════════════════════════════════════

def test_update_rejects_invalid_status_string():
    """传入非法 status 字符串时，update() 应返回 False，不修改原对象"""
    store = InMemoryItemStore()
    item = _make_item("TEST01")
    store.add(item)

    # 当前 item 状态是 pending
    assert store.get("TEST01").status == TaskStatus.pending

    # 尝试传入非法 status 字符串
    result = store.update("TEST01", status="not_a_real_status")

    # 应该被拒绝
    assert result is False
    # 原对象不应被修改
    assert store.get("TEST01").status == TaskStatus.pending


# ═══════════════════════════════════════════════════
# 增量测试 2: update() 应接受合法的 TaskStatus Enum
# ═══════════════════════════════════════════════════

def test_update_accepts_valid_task_status():
    """传入合法的 TaskStatus Enum 时，update() 应成功更新"""
    store = InMemoryItemStore()
    item = _make_item("TEST02")
    store.add(item)

    result = store.update("TEST02", status=TaskStatus.downloading)
    assert result is True
    assert store.get("TEST02").status == TaskStatus.downloading


# ═══════════════════════════════════════════════════
# 增量测试 3: update() 应接受合法的 status 字符串（Pydantic 自动转换）
# ═══════════════════════════════════════════════════

def test_update_accepts_valid_status_string():
    """传入合法的 status 字符串时，Pydantic 应自动转换为 TaskStatus"""
    store = InMemoryItemStore()
    item = _make_item("TEST03")
    store.add(item)

    result = store.update("TEST03", status="downloading")
    assert result is True
    assert store.get("TEST03").status == TaskStatus.downloading


# ═══════════════════════════════════════════════════
# 增量测试 4: update() 应拒绝非法 progress 值
# ═══════════════════════════════════════════════════

def test_update_rejects_invalid_progress():
    """传入非法 progress 值（如字符串）时，update() 应返回 False"""
    store = InMemoryItemStore()
    item = _make_item("TEST04")
    store.add(item)

    result = store.update("TEST04", progress="not_a_number")
    assert result is False
    assert store.get("TEST04").progress == 0.0


# ═══════════════════════════════════════════════════
# 增量测试 5: update() 更新后应产生新的不可变实例
# ═══════════════════════════════════════════════════

def test_update_creates_new_instance():
    """update() 应通过 model_copy() 创建新实例，保持不可变性"""
    store = InMemoryItemStore()
    item = _make_item("TEST05")
    store.add(item)

    original = store.get("TEST05")
    store.update("TEST05", category="电影")
    updated = store.get("TEST05")

    # 应该是不同的对象实例
    assert updated is not original
    # 原对象不应被修改
    assert original.category is None
    # 新对象应有更新值
    assert updated.category == "电影"


# ═══════════════════════════════════════════════════
# 增量测试 6: update() 应拒绝不存在的字段名
# ═══════════════════════════════════════════════════

def test_update_rejects_unknown_fields():
    """传入 MagnetItem 不存在的字段时，update() 应返回 False"""
    store = InMemoryItemStore()
    item = _make_item("TEST06")
    store.add(item)

    result = store.update("TEST06", not_a_field="value")
    assert result is False


# ═══════════════════════════════════════════════════
# 增量测试 7: 批量更新应保持原子性（全部成功或全部失败）
# ═══════════════════════════════════════════════════

def test_update_multiple_fields_atomic():
    """同时更新多个字段时，如果任一字段非法，全部不应生效"""
    store = InMemoryItemStore()
    item = _make_item("TEST07")
    store.add(item)

    # 尝试同时更新合法和非法字段
    result = store.update("TEST07", category="电影", status="bad_status")

    # 应该全部失败
    assert result is False
    assert store.get("TEST07").category is None
    assert store.get("TEST07").status == TaskStatus.pending


if __name__ == "__main__":
    test_update_rejects_invalid_status_string()
    print("[PASS] test_update_rejects_invalid_status_string")

    test_update_accepts_valid_task_status()
    print("[PASS] test_update_accepts_valid_task_status")

    test_update_accepts_valid_status_string()
    print("[PASS] test_update_accepts_valid_status_string")

    test_update_rejects_invalid_progress()
    print("[PASS] test_update_rejects_invalid_progress")

    test_update_creates_new_instance()
    print("[PASS] test_update_creates_new_instance")

    test_update_rejects_unknown_fields()
    print("[PASS] test_update_rejects_unknown_fields")

    test_update_multiple_fields_atomic()
    print("[PASS] test_update_multiple_fields_atomic")

    print("\n=== TDD Loop 1: All store validation tests passed! ===")
