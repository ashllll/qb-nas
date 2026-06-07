"""
ItemStore — 磁力链接中央存储（深模块）

可替换适配器：
- InMemoryItemStore: 默认内存实现，用于单进程
- RedisItemStore: 可持久化 + 多进程共享（未来）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, runtime_checkable

from magnet_harvester.models import MagnetItem, TaskStatus

log = logging.getLogger(__name__)


@dataclass
class StoreStats:
    total: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)
    by_status: Dict[str, int] = field(default_factory=dict)
    pending_count: int = 0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "by_category": dict(self.by_category),
            "by_status": dict(self.by_status),
            "pending_count": self.pending_count,
        }


@runtime_checkable
class ItemStore(Protocol):
    """ItemStore 协议 — 所有 store 适配器必须实现此接口"""

    def add(self, item: MagnetItem) -> bool: ...
    def get(self, hash_key: str) -> Optional[MagnetItem]: ...
    def update(self, hash_key: str, **fields) -> bool: ...
    def remove(self, hash_key: str) -> bool: ...
    def list(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[MagnetItem]: ...
    def search(self, query: str) -> List[MagnetItem]: ...
    def get_pending(self) -> List[MagnetItem]: ...
    def get_hashes_by_prefix(self, prefix: str) -> List[str]: ...
    @property
    def count(self) -> int: ...
    def stats(self) -> StoreStats: ...
    def add_batch(self, items: List[MagnetItem]) -> int: ...
    def clear(self): ...


class InMemoryItemStore:
    """ItemStore 的默认内存适配器。

    接口：add / get / update / remove / list / search / clear / stats / count
    — 7 个调用点共享 1 个接口
    """

    def __init__(self):
        self._items: Dict[str, MagnetItem] = {}
        self._listeners: List[Callable[[str, MagnetItem], None]] = []

    # ── 核心操作 ──────────────────────────────

    def add(self, item: MagnetItem) -> bool:
        """添加条目，已存在返回 False（全局去重）"""
        if item.hash in self._items:
            return False
        self._items[item.hash] = item
        self._notify("add", item)
        return True

    def get(self, hash_key: str) -> Optional[MagnetItem]:
        return self._items.get(hash_key)

    def update(self, hash_key: str, **fields) -> bool:
        """更新字段（category, save_path, status, error_msg 等）"""
        item = self._items.get(hash_key)
        if not item:
            return False
        for k, v in fields.items():
            if hasattr(item, k):
                setattr(item, k, v)
        self._notify("update", item)
        return True

    def remove(self, hash_key: str) -> bool:
        if hash_key in self._items:
            del self._items[hash_key]
            return True
        return False

    # ── 查询 ──────────────────────────────

    def list(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[MagnetItem]:
        items = list(self._items.values())
        if category:
            items = [i for i in items if i.category == category]
        if status and status != "all":
            items = [i for i in items if i.status.value == status]
        items.sort(key=lambda i: i.name.lower())
        return items[:limit]

    def search(self, query: str) -> List[MagnetItem]:
        q = query.lower()
        return [i for i in self._items.values() if q in i.name.lower()]

    def get_pending(self) -> List[MagnetItem]:
        return [
            i for i in self._items.values()
            if i.status == TaskStatus.pending and i.category
        ]

    def get_hashes_by_prefix(self, prefix: str) -> List[str]:
        """支持通过 hash 前缀查找完整 hash（Agent 用）"""
        p = prefix.lower()
        return [h for h in self._items if h.lower().startswith(p)]

    # ── 统计 ──────────────────────────────

    @property
    def count(self) -> int:
        return len(self._items)

    def stats(self) -> StoreStats:
        s = StoreStats()
        for item in self._items.values():
            s.total += 1
            cat = item.category or "未分类"
            s.by_category[cat] = s.by_category.get(cat, 0) + 1
            st = item.status.value
            s.by_status[st] = s.by_status.get(st, 0) + 1
            if item.status == TaskStatus.pending:
                s.pending_count += 1
        return s

    # ── 批量操作 ──────────────────────────────

    def add_batch(self, items: List[MagnetItem]) -> int:
        """批量添加，返回新增数量"""
        added = 0
        for item in items:
            if self.add(item):
                added += 1
        return added

    def clear(self):
        self._items.clear()

    # ── 变更监听（MessageBus 集成） ──────────────

    def on_change(self, listener: Callable[[str, MagnetItem], None]):
        self._listeners.append(listener)

    def _notify(self, action: str, item: MagnetItem):
        for listener in self._listeners:
            try:
                listener(action, item)
            except Exception:
                pass


class FakeStore:
    """测试用 FakeStore — 实现 ItemStore 协议，纯内存，无 listener"""

    def __init__(self):
        self._items: Dict[str, MagnetItem] = {}

    def add(self, item: MagnetItem) -> bool:
        if item.hash in self._items:
            return False
        self._items[item.hash] = item
        return True

    def get(self, hash_key: str) -> Optional[MagnetItem]:
        return self._items.get(hash_key)

    def update(self, hash_key: str, **fields) -> bool:
        item = self._items.get(hash_key)
        if not item:
            return False
        for k, v in fields.items():
            if hasattr(item, k):
                setattr(item, k, v)
        return True

    def remove(self, hash_key: str) -> bool:
        if hash_key in self._items:
            del self._items[hash_key]
            return True
        return False

    def list(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[MagnetItem]:
        items = list(self._items.values())
        if category:
            items = [i for i in items if i.category == category]
        if status and status != "all":
            items = [i for i in items if i.status.value == status]
        items.sort(key=lambda i: i.name.lower())
        return items[:limit]

    def search(self, query: str) -> List[MagnetItem]:
        q = query.lower()
        return [i for i in self._items.values() if q in i.name.lower()]

    def get_pending(self) -> List[MagnetItem]:
        return [
            i for i in self._items.values()
            if i.status == TaskStatus.pending and i.category
        ]

    def get_hashes_by_prefix(self, prefix: str) -> List[str]:
        p = prefix.lower()
        return [h for h in self._items if h.lower().startswith(p)]

    @property
    def count(self) -> int:
        return len(self._items)

    def stats(self) -> StoreStats:
        s = StoreStats()
        for item in self._items.values():
            s.total += 1
            cat = item.category or "未分类"
            s.by_category[cat] = s.by_category.get(cat, 0) + 1
            st = item.status.value
            s.by_status[st] = s.by_status.get(st, 0) + 1
            if item.status == TaskStatus.pending:
                s.pending_count += 1
        return s

    def add_batch(self, items: List[MagnetItem]) -> int:
        added = 0
        for item in items:
            if self.add(item):
                added += 1
        return added

    def clear(self):
        self._items.clear()
