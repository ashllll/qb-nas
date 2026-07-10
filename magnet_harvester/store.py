"""
ItemStore — 磁力链接中央存储（深模块）

可替换适配器：
- InMemoryItemStore: 默认内存实现，用于单进程
- SQLiteItemStore: 持久化实现（基于 sqlite3）
- RedisItemStore: 可持久化 + 多进程共享（未来）
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

from pydantic import ValidationError

from magnet_harvester.models import MagnetItem, TaskStatus

log = logging.getLogger(__name__)


def _item_name_key(item: MagnetItem) -> str:
    return item.name.lower()


def _escape_like(s: str) -> str:
    """转义 SQL LIKE 通配符 % 和 _，防止用户输入被解释为模式匹配。

    ESCAPE '\\' 配合使用：先转义 \\，再转义 % 和 _。
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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


class _ItemStoreBackend(Protocol):
    """Module-internal synchronous adapter interface."""

    def add(self, item: MagnetItem) -> bool: ...
    def get(self, hash_key: str) -> Optional[MagnetItem]: ...
    def update(self, hash_key: str, **fields) -> bool: ...
    def remove(self, hash_key: str) -> bool: ...
    def list(
        self,
        category: Optional[str] = None,
        status: Optional[str | list[str] | set[str]] = None,
        limit: int = 20,
    ) -> List[MagnetItem]: ...
    def search(self, query: str, limit: Optional[int] = None) -> List[MagnetItem]: ...
    def get_pending(self) -> List[MagnetItem]: ...
    def get_hashes_by_prefix(self, prefix: str) -> List[str]: ...
    @property
    def count(self) -> int: ...
    def count_items(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int: ...
    def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple: ...
    def stats(self) -> StoreStats: ...
    def add_batch(self, items: List[MagnetItem]) -> int: ...
    def clear(self) -> int: ...


class ItemStore(Protocol):
    """Typed async interface for Magnet item persistence and queries."""

    async def add(self, item: MagnetItem) -> bool: ...
    async def get(self, hash_key: str) -> Optional[MagnetItem]: ...
    async def update(self, hash_key: str, **fields) -> bool: ...
    async def remove(self, hash_key: str) -> bool: ...
    async def list(
        self,
        category: Optional[str] = None,
        status: Optional[str | list[str] | set[str]] = None,
        limit: int = 20,
    ) -> List[MagnetItem]: ...
    async def search(self, query: str, limit: Optional[int] = None) -> List[MagnetItem]: ...
    async def get_pending(self) -> List[MagnetItem]: ...
    async def get_hashes_by_prefix(self, prefix: str) -> List[str]: ...
    async def count(self) -> int: ...
    async def count_items(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int: ...
    async def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple: ...
    async def stats(self) -> StoreStats: ...
    async def add_batch(self, items: List[MagnetItem]) -> int: ...
    async def clear(self) -> int: ...


class AsyncItemStore:
    """Typed async interface over a synchronous Magnet item store adapter.

    Callers never need to know whether the selected adapter blocks the event
    loop. SQLite work is moved to a worker thread here; in-memory work stays
    inline so its cheap operations avoid thread scheduling overhead.
    """

    def __init__(self, backend: _ItemStoreBackend):
        self._backend = backend

    async def _invoke(self, operation, /, *args, **kwargs):
        if getattr(self._backend, "blocks_event_loop", False):
            return await asyncio.to_thread(operation, *args, **kwargs)
        return operation(*args, **kwargs)

    async def add(self, item: MagnetItem) -> bool:
        return await self._invoke(self._backend.add, item)

    async def get(self, hash_key: str) -> Optional[MagnetItem]:
        return await self._invoke(self._backend.get, hash_key)

    async def update(self, hash_key: str, **fields) -> bool:
        return await self._invoke(self._backend.update, hash_key, **fields)

    async def remove(self, hash_key: str) -> bool:
        return await self._invoke(self._backend.remove, hash_key)

    async def list(
        self,
        category: Optional[str] = None,
        status: Optional[str | list[str] | set[str]] = None,
        limit: int = 20,
    ) -> List[MagnetItem]:
        return await self._invoke(
            self._backend.list,
            category=category,
            status=status,
            limit=limit,
        )

    async def search(self, query: str, limit: Optional[int] = None) -> List[MagnetItem]:
        return await self._invoke(self._backend.search, query, limit=limit)

    async def get_pending(self) -> List[MagnetItem]:
        return await self._invoke(self._backend.get_pending)

    async def get_hashes_by_prefix(self, prefix: str) -> List[str]:
        return await self._invoke(self._backend.get_hashes_by_prefix, prefix)

    async def count(self) -> int:
        if getattr(self._backend, "blocks_event_loop", False):
            return await asyncio.to_thread(getattr, self._backend, "count")
        return self._backend.count

    async def count_items(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        return await self._invoke(
            self._backend.count_items,
            category=category,
            status=status,
        )

    async def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple:
        return await self._invoke(
            self._backend.count_and_page,
            category=category,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def stats(self) -> StoreStats:
        return await self._invoke(self._backend.stats)

    async def add_batch(self, items: List[MagnetItem]) -> int:
        return await self._invoke(self._backend.add_batch, items)

    async def clear(self) -> int:
        return await self._invoke(self._backend.clear)


class InMemoryItemStore:
    """ItemStore 的默认内存适配器。

    接口：add / get / update / remove / list / search / clear / stats / count
    — 7 个调用点共享 1 个接口
    """

    blocks_event_loop = False

    def __init__(self):
        self._items: Dict[str, MagnetItem] = {}
        self._lock = threading.Lock()

    # ── 核心操作 ──────────────────────────────

    def add(self, item: MagnetItem) -> bool:
        """添加条目，已存在返回 False（全局去重）"""
        with self._lock:
            if item.hash in self._items:
                return False
            self._items[item.hash] = item
            return True

    def get(self, hash_key: str) -> Optional[MagnetItem]:
        with self._lock:
            return self._items.get(hash_key)

    def update(self, hash_key: str, **fields) -> bool:
        """更新字段（category, save_path, status, error_msg 等）

        通过 Pydantic model_validate 重新验证，保持类型安全。
        如果任何字段非法或不存在，返回 False 且不修改原对象。
        """
        with self._lock:
            item = self._items.get(hash_key)
            if not item:
                return False

            # 拒绝未知字段
            unknown = [k for k in fields if k not in MagnetItem.model_fields]
            if unknown:
                return False

            data = item.model_dump()
            data.update(fields)
            data["updated_at"] = datetime.now()
            try:
                new_item = MagnetItem.model_validate(data)
            except ValidationError:
                return False

            self._items[hash_key] = new_item
            return True

    def remove(self, hash_key: str) -> bool:
        with self._lock:
            if hash_key in self._items:
                del self._items[hash_key]
                return True
            return False

    # ── 查询 ──────────────────────────────

    def list(
        self,
        category: Optional[str] = None,
        status: Optional[str | list[str] | set[str]] = None,
        limit: int = 20,
    ) -> List[MagnetItem]:
        if limit <= 0:
            return []
        with self._lock:
            return heapq.nsmallest(
                limit,
                self._iter_filtered_items(category=category, status=status),
                key=_item_name_key,
            )

    def _iter_filtered_items(
        self,
        category: Optional[str] = None,
        status: Optional[str | list[str] | set[str]] = None,
    ) -> Iterable[MagnetItem]:
        status_set: set[str] | None = None
        if isinstance(status, (list, set, frozenset)):
            if not status:
                return
            status_set = set(status)
        elif status and status != "all":
            status_set = {status}

        for item in self._items.values():
            if category and item.category != category:
                continue
            if status_set is not None and item.status.value not in status_set:
                continue
            yield item

    def search(self, query: str, limit: Optional[int] = None) -> List[MagnetItem]:
        if limit is not None and limit <= 0:
            return []
        q = query.lower()
        with self._lock:
            results = [i for i in self._items.values() if q in i.name.lower()]
        if limit is not None:
            return results[:limit]
        return results

    def get_pending(self) -> List[MagnetItem]:
        with self._lock:
            return [
                i for i in self._items.values() if i.status == TaskStatus.pending and i.category
            ]

    def get_hashes_by_prefix(self, prefix: str) -> List[str]:
        """支持通过 hash 前缀查找完整 hash（Agent 用）"""
        p = prefix.lower()
        with self._lock:
            return [h for h in self._items.keys() if h.lower().startswith(p)]

    # ── 统计 ──────────────────────────────

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def count_items(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        with self._lock:
            return sum(
                1
                for item in self._items.values()
                if (category is None or item.category == category)
                and (status is None or status == "all" or item.status.value == status)
            )

    def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple:
        """单次持锁返回 total + 分页 items，消除 count→list 之间的 TOCTOU。"""
        with self._lock:
            filtered = list(self._iter_filtered_items(category=category, status=status))
            if limit <= 0:
                return len(filtered), []
            offset = max(0, offset)
            sorted_items = heapq.nsmallest(offset + limit, filtered, key=_item_name_key)
            return len(filtered), sorted_items[offset : offset + limit]

    def stats(self) -> StoreStats:
        with self._lock:
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
        with self._lock:
            pending: dict[str, MagnetItem] = {}
            for item in items:
                if item.hash in self._items or item.hash in pending:
                    continue
                pending[item.hash] = item
            self._items.update(pending)
            return len(pending)

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
        return count


# FakeStore — 测试用别名，与 InMemoryItemStore 逻辑相同
FakeStore = InMemoryItemStore


# ═══════════════════════════════════════════════════
# SQLiteItemStore — 持久化存储适配器
# ═══════════════════════════════════════════════════


class SQLiteItemStore:
    """SQLite 持久化 ItemStore 适配器，实现 ItemStore Protocol（同步接口）。

    使用标准库 sqlite3 + threading.Lock 实现线程安全的持久化存储。
    所有公开方法保持同步签名，与现有 ItemStore Protocol 完全兼容。
    """

    blocks_event_loop = True

    def __init__(self, db_path: str | Path = "data/magnet_items.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ── 数据库连接 ──────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), isolation_level="DEFERRED")
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            raise
        return closing(conn)

    def _init_db(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS magnet_items (
                    hash TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    magnet TEXT NOT NULL,
                    size TEXT,
                    source_url TEXT DEFAULT '',
                    category TEXT,
                    save_path TEXT,
                    status TEXT DEFAULT 'pending',
                    progress REAL DEFAULT 0.0,
                    torrent_state TEXT,
                    error_msg TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            db.commit()

    @staticmethod
    def _item_to_row(item: MagnetItem) -> dict:
        return {
            "hash": item.hash,
            "name": item.name,
            "magnet": item.magnet,
            "size": item.size or "",
            "source_url": item.source_url,
            "category": item.category or "",
            "save_path": item.save_path or "",
            "status": item.status.value,
            "progress": item.progress,
            "torrent_state": item.torrent_state or "",
            "error_msg": item.error_msg or "",
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _row_to_item(row: sqlite3.Row | None) -> MagnetItem | None:
        if row is None:
            return None
        d: dict = dict(row)
        try:
            for field_name in ("category", "save_path", "torrent_state", "error_msg", "size"):
                if field_name in d and d[field_name] == "":
                    d[field_name] = None
            if "created_at" in d and isinstance(d["created_at"], str):
                d["created_at"] = datetime.fromisoformat(d["created_at"])
            if "updated_at" in d and isinstance(d["updated_at"], str):
                d["updated_at"] = datetime.fromisoformat(d["updated_at"])
            return MagnetItem(**d)
        except (ValidationError, ValueError, TypeError) as e:
            hash_val = d.get("hash", "unknown")
            log.error("sqlite 行反序列化失败 hash=%s: %s", hash_val, e)
            return None

    # ── 核心操作 ──────────────────────────────

    def add(self, item: MagnetItem) -> bool:
        with self._lock, self._connect() as db:
            row = self._item_to_row(item)
            try:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO magnet_items
                       (hash, name, magnet, size, source_url, category, save_path,
                        status, progress, torrent_state, error_msg, created_at, updated_at)
                       VALUES (:hash, :name, :magnet, :size, :source_url, :category, :save_path,
                        :status, :progress, :torrent_state, :error_msg, :created_at, :updated_at)""",
                    row,
                )
                db.commit()
                return cursor.rowcount > 0
            except sqlite3.OperationalError:
                log.error("sqlite: add(%s) 数据库损坏", item.hash[:16], exc_info=True)
                return False
            except (TypeError, ValueError) as e:
                log.error("sqlite: add(%s) 数据序列化错误: %s", item.hash[:16], e, exc_info=True)
                raise

    def get(self, hash_key: str) -> Optional[MagnetItem]:
        with self._lock, self._connect() as db:
            cursor = db.execute("SELECT * FROM magnet_items WHERE hash = ?", (hash_key,))
            return self._row_to_item(cursor.fetchone())

    def update(self, hash_key: str, **fields) -> bool:
        known = MagnetItem.model_fields
        unknown = [k for k in fields if k not in known]
        if unknown:
            return False

        with self._lock, self._connect() as db:
            # Read + validate + write in a single lock scope (avoids TOCTOU)
            cursor = db.execute("SELECT * FROM magnet_items WHERE hash = ?", (hash_key,))
            row = cursor.fetchone()
            if row is None:
                return False
            item = self._row_to_item(row)
            if item is None:
                return False

            try:
                data = item.model_dump()
                data.update(fields)
                data["updated_at"] = datetime.now()
                new_item = MagnetItem.model_validate(data)
            except ValidationError:
                return False

            row_data = self._item_to_row(new_item)
            cursor = db.execute(
                """UPDATE magnet_items SET
                   name=:name, magnet=:magnet, size=:size, source_url=:source_url,
                   category=:category, save_path=:save_path, status=:status,
                   progress=:progress, torrent_state=:torrent_state, error_msg=:error_msg,
                   created_at=:created_at, updated_at=:updated_at
                WHERE hash = :hash""",
                row_data,
            )
            db.commit()
            return cursor.rowcount > 0

    def remove(self, hash_key: str) -> bool:
        with self._lock, self._connect() as db:
            cursor = db.execute("DELETE FROM magnet_items WHERE hash = ?", (hash_key,))
            db.commit()
            return cursor.rowcount > 0

    # ── 查询 ──────────────────────────────

    def list(
        self,
        category: Optional[str] = None,
        status: Optional[str | list[str] | set[str]] = None,
        limit: int = 20,
    ) -> List[MagnetItem]:
        if limit <= 0:
            return []

        conditions: list[str] = []
        params: list[str | int] = []
        if category:
            conditions.append("category = ?")
            params.append(category)
        if status and status != "all":
            if isinstance(status, (list, set, frozenset)):
                if not status:
                    return []
                placeholders = ", ".join(["?"] * len(status))
                conditions.append(f"status IN ({placeholders})")
                params.extend(status)
            else:
                conditions.append("status = ?")
                params.append(status)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM magnet_items {where} ORDER BY name ASC LIMIT ?"
        params.append(limit)

        with self._lock, self._connect() as db:
            cursor = db.execute(sql, params)
            return [item for r in cursor.fetchall() if (item := self._row_to_item(r)) is not None]

    def search(self, query: str, limit: Optional[int] = None) -> List[MagnetItem]:
        if limit is not None and limit <= 0:
            return []
        q = f"%{_escape_like(query)}%"
        sql = "SELECT * FROM magnet_items WHERE LOWER(name) LIKE LOWER(?) ESCAPE '\\'"
        params: tuple = (q,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (q, limit)
        with self._lock, self._connect() as db:
            cursor = db.execute(sql, params)
            return [item for r in cursor.fetchall() if (item := self._row_to_item(r)) is not None]

    def get_pending(self) -> List[MagnetItem]:
        pending_status = TaskStatus.pending.value
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "SELECT * FROM magnet_items WHERE status = ? AND category IS NOT NULL AND category != ''",
                (pending_status,),
            )
            return [item for r in cursor.fetchall() if (item := self._row_to_item(r)) is not None]

    def get_hashes_by_prefix(self, prefix: str) -> List[str]:
        p = f"{_escape_like(prefix)}%"
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "SELECT hash FROM magnet_items WHERE hash LIKE ? ESCAPE '\\'",
                (p,),
            )
            return [r[0] for r in cursor.fetchall()]

    # ── 统计 ──────────────────────────────

    @property
    def count(self) -> int:
        with self._lock, self._connect() as db:
            cursor = db.execute("SELECT COUNT(*) FROM magnet_items")
            row = cursor.fetchone()
            return row[0] if row else 0

    def count_items(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        conditions: list[str] = []
        params: list[str] = []
        if category:
            conditions.append("category = ?")
            params.append(category)
        if status and status != "all":
            conditions.append("status = ?")
            params.append(status)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT COUNT(*) FROM magnet_items {where}"

        with self._lock, self._connect() as db:
            cursor = db.execute(sql, params)
            row = cursor.fetchone()
            return row[0] if row else 0

    def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple:
        """单次持锁+单事务返回 total + 分页 items，消除 count→list 之间的 TOCTOU。"""
        conditions: list[str] = []
        params: list[str] = []
        if category:
            conditions.append("category = ?")
            params.append(category)
        if status and status != "all":
            conditions.append("status = ?")
            params.append(status)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        with self._lock, self._connect() as db:
            cursor = db.execute(f"SELECT COUNT(*) FROM magnet_items {where}", params)
            row = cursor.fetchone()
            total = row[0] if row else 0

            if limit <= 0:
                return total, []
            offset = max(0, offset)
            cursor = db.execute(
                f"SELECT * FROM magnet_items {where} ORDER BY name ASC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            items = [item for r in cursor.fetchall() if (item := self._row_to_item(r)) is not None]

        return total, items

    def stats(self) -> StoreStats:
        s = StoreStats()
        with self._lock, self._connect() as db:
            # Total count
            cursor = db.execute("SELECT COUNT(*) FROM magnet_items")
            row = cursor.fetchone()
            s.total = row[0] if row else 0

            # By category
            cursor = db.execute(
                "SELECT CASE WHEN category IS NULL OR category = '' THEN '未分类' ELSE category END as cat, "
                "COUNT(*) as cnt FROM magnet_items GROUP BY cat"
            )
            for row in cursor.fetchall():
                s.by_category[row["cat"]] = row["cnt"]

            # By status
            cursor = db.execute("SELECT status, COUNT(*) as cnt FROM magnet_items GROUP BY status")
            for row in cursor.fetchall():
                s.by_status[row["status"]] = row["cnt"]

            # Pending count
            cursor = db.execute(
                "SELECT COUNT(*) FROM magnet_items WHERE status = ?",
                (TaskStatus.pending.value,),
            )
            row = cursor.fetchone()
            s.pending_count = row[0] if row else 0

        return s

    # ── 批量操作 ──────────────────────────────

    def add_batch(self, items: List[MagnetItem]) -> int:
        if not items:
            return 0
        added = 0
        committed = 0
        _BATCH_COMMIT = 50
        with self._lock, self._connect() as db:
            for i, item in enumerate(items):
                row = self._item_to_row(item)
                db.execute("SAVEPOINT add_item")
                try:
                    cur = db.execute(
                        """INSERT OR IGNORE INTO magnet_items
                           (hash, name, magnet, size, source_url, category, save_path,
                            status, progress, torrent_state, error_msg, created_at, updated_at)
                           VALUES (:hash, :name, :magnet, :size, :source_url, :category, :save_path,
                            :status, :progress, :torrent_state, :error_msg, :created_at, :updated_at)""",
                        row,
                    )
                    if cur.rowcount > 0:
                        added += 1
                    db.execute("RELEASE SAVEPOINT add_item")
                except sqlite3.OperationalError:
                    log.error(
                        "sqlite: add_batch 条目 %s 数据库损坏",
                        item.hash[:16] if item.hash else "?",
                        exc_info=True,
                    )
                    db.execute("ROLLBACK TO SAVEPOINT add_item")
                    db.execute("RELEASE SAVEPOINT add_item")
                except Exception:
                    log.exception(
                        "sqlite: add_batch 条目 %s 未知错误", item.hash[:16] if item.hash else "?"
                    )
                    db.execute("ROLLBACK TO SAVEPOINT add_item")
                    db.execute("RELEASE SAVEPOINT add_item")
                # 每 _BATCH_COMMIT 条提交一次，防止最终 commit 失败导致全部丢失
                if (i + 1) % _BATCH_COMMIT == 0:
                    try:
                        db.commit()
                    except sqlite3.Error:
                        db.rollback()
                        raise
                    committed = added
            # 提交剩余条目
            if added > committed:
                try:
                    db.commit()
                except sqlite3.Error:
                    db.rollback()
                    raise
        if added < len(items):
            log.warning("sqlite: add_batch 部分成功 %d/%d", added, len(items))
        return added

    def clear(self) -> int:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT COUNT(*) FROM magnet_items").fetchone()
            count = row[0] if row else 0
            db.execute("DELETE FROM magnet_items")
            db.commit()
        return count
