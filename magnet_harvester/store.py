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
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

from pydantic import ValidationError

from magnet_harvester.models import SYNC_ACTIVE_STATUSES, MagnetItem, TaskStatus
from magnet_harvester.utils.timeutil import iso_utc, parse_iso_utc, utc_now

log = logging.getLogger(__name__)

# 同步分页必须走部分索引：优化器在状态索引与部分索引之间可能选错，选错就会
# 全表扫 + 临时 B-tree 排序；这里显式钉住索引，索引不存在时直接报错而不是悄悄退化。
_SYNC_PAGE_SQL = (
    "SELECT * FROM magnet_items INDEXED BY idx_magnet_items_sync "
    "WHERE {where} ORDER BY hash ASC LIMIT ?"
)


def _item_name_key(item: MagnetItem) -> str:
    return item.name.lower()


def _escape_like(s: str) -> str:
    """转义 SQL LIKE 通配符 % 和 _，防止用户输入被解释为模式匹配。

    ESCAPE '\\' 配合使用：先转义 \\，再转义 % 和 _。
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validated_update(item: MagnetItem, fields: dict) -> MagnetItem | None:
    """构造经过 Pydantic 校验的新条目，不修改原对象。"""
    if "hash" in fields or any(key not in MagnetItem.model_fields for key in fields):
        return None
    data = item.model_dump()
    data.update(fields)
    data["updated_at"] = utc_now()
    try:
        return MagnetItem.model_validate(data)
    except ValidationError:
        return None


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
    def get_many(self, hashes: Iterable[str]) -> Dict[str, MagnetItem]: ...
    def update(self, hash_key: str, **fields) -> Optional[MagnetItem]: ...
    def update_if_status(
        self,
        hash_key: str,
        expected_statuses: set[TaskStatus],
        **fields,
    ) -> Optional[MagnetItem]: ...
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
    def list_for_sync(
        self, after_hash: Optional[str] = None, limit: int = 500
    ) -> List[MagnetItem]: ...
    def recent(self, limit: int = 10) -> List[MagnetItem]: ...
    @property
    def count(self) -> int: ...
    def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "name",
    ) -> tuple: ...
    def stats(self) -> StoreStats: ...
    def clear(self) -> int: ...


class ItemStore(Protocol):
    """Typed async interface for Magnet item persistence and queries."""

    async def add(self, item: MagnetItem) -> bool: ...
    async def get(self, hash_key: str) -> Optional[MagnetItem]: ...
    async def get_many(self, hashes: Iterable[str]) -> Dict[str, MagnetItem]: ...
    async def update(self, hash_key: str, **fields) -> Optional[MagnetItem]: ...
    async def update_if_status(
        self,
        hash_key: str,
        expected_statuses: set[TaskStatus],
        **fields,
    ) -> Optional[MagnetItem]: ...
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
    async def list_for_sync(
        self, after_hash: Optional[str] = None, limit: int = 500
    ) -> List[MagnetItem]: ...
    async def recent(self, limit: int = 10) -> List[MagnetItem]: ...
    async def count(self) -> int: ...
    async def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "name",
    ) -> tuple: ...
    async def stats(self) -> StoreStats: ...
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

    async def get_many(self, hashes: Iterable[str]) -> Dict[str, MagnetItem]:
        return await self._invoke(self._backend.get_many, hashes)

    async def update(self, hash_key: str, **fields) -> Optional[MagnetItem]:
        return await self._invoke(self._backend.update, hash_key, **fields)

    async def update_if_status(
        self,
        hash_key: str,
        expected_statuses: set[TaskStatus],
        **fields,
    ) -> Optional[MagnetItem]:
        """仅当当前状态匹配时更新；比较与写入在同一把锁/同一条 SQL 里完成。"""
        return await self._invoke(
            self._backend.update_if_status,
            hash_key,
            expected_statuses,
            **fields,
        )

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

    async def list_for_sync(
        self, after_hash: Optional[str] = None, limit: int = 500
    ) -> List[MagnetItem]:
        return await self._invoke(self._backend.list_for_sync, after_hash=after_hash, limit=limit)

    async def recent(self, limit: int = 10) -> List[MagnetItem]:
        return await self._invoke(self._backend.recent, limit)

    async def count(self) -> int:
        if getattr(self._backend, "blocks_event_loop", False):
            return await asyncio.to_thread(getattr, self._backend, "count")
        return self._backend.count

    async def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "name",
    ) -> tuple:
        return await self._invoke(
            self._backend.count_and_page,
            category=category,
            status=status,
            limit=limit,
            offset=offset,
            order=order,
        )

    async def stats(self) -> StoreStats:
        return await self._invoke(self._backend.stats)

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

    def get_many(self, hashes: Iterable[str]) -> Dict[str, MagnetItem]:
        with self._lock:
            return {h: self._items[h] for h in dict.fromkeys(hashes) if h in self._items}

    def update(self, hash_key: str, **fields) -> Optional[MagnetItem]:
        """更新字段（category, save_path, status, error_msg 等）

        通过 Pydantic model_validate 重新验证，保持类型安全。
        返回写入后的最新条目；字段非法或条目不存在时返回 None。
        """
        with self._lock:
            item = self._items.get(hash_key)
            if not item:
                return None

            new_item = _validated_update(item, fields)
            if new_item is None:
                return None

            self._items[hash_key] = new_item
            return new_item

    def update_if_status(
        self,
        hash_key: str,
        expected_statuses: set[TaskStatus],
        **fields,
    ) -> Optional[MagnetItem]:
        """仅当当前状态匹配时更新；状态比较与写入共享同一把锁。"""
        if not expected_statuses:
            return None
        with self._lock:
            item = self._items.get(hash_key)
            if item is None or item.status not in expected_statuses:
                return None

            new_item = _validated_update(item, fields)
            if new_item is None:
                return None

            self._items[hash_key] = new_item
            return new_item

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
        if isinstance(status, (list, set, frozenset, tuple)):
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

    def recent(self, limit: int = 10) -> List[MagnetItem]:
        """最近入库的条目（新→旧）。"""
        if limit <= 0:
            return []
        with self._lock:
            ordered = sorted(
                self._items.values(),
                key=lambda i: (i.created_at, i.updated_at),
                reverse=True,
            )
            return ordered[:limit]

    def get_hashes_by_prefix(self, prefix: str) -> List[str]:
        """支持通过 hash 前缀查找完整 hash（Agent 用）"""
        p = prefix.lower()
        with self._lock:
            return [h for h in self._items.keys() if h.lower().startswith(p)]

    def list_for_sync(self, after_hash: Optional[str] = None, limit: int = 500) -> List[MagnetItem]:
        """按 hash 顺序取一页活跃条目；keyset 分页，不打乱顺序、不重扫头部。"""
        if limit <= 0:
            return []
        active = set(SYNC_ACTIVE_STATUSES)
        with self._lock:
            candidates = (
                item
                for item in self._items.values()
                if item.status in active and (after_hash is None or item.hash > after_hash)
            )
            return heapq.nsmallest(limit, candidates, key=lambda item: item.hash)

    # ── 统计 ──────────────────────────────

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "name",
    ) -> tuple:
        """单次持锁返回 total + 分页 items，消除 count→list 之间的 TOCTOU。"""
        with self._lock:
            filtered = list(self._iter_filtered_items(category=category, status=status))
            if limit <= 0:
                return len(filtered), []
            offset = max(0, offset)
            if order == "newest":
                ordered = sorted(
                    filtered,
                    key=lambda i: (i.created_at, i.updated_at),
                    reverse=True,
                )
                return len(filtered), ordered[offset : offset + limit]
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
                    detail_url TEXT DEFAULT '',
                    batch_id TEXT DEFAULT '',
                    source_kind TEXT DEFAULT '',
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
            self._migrate_columns(db)
            self._create_indexes(db)
            db.commit()

    @classmethod
    def _create_indexes(cls, db: sqlite3.Connection) -> None:
        """补查询基线索引；旧库启动时幂等补齐，不改数据。

        sync 索引是部分索引：只收活跃状态，按 hash 排序，qB 同步的 keyset
        分页因此既不用扫全表，也不会为 ORDER BY 付临时 B-tree。
        """
        db.execute("CREATE INDEX IF NOT EXISTS idx_magnet_items_status ON magnet_items(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_magnet_items_category ON magnet_items(category)")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_magnet_items_created_at "
            "ON magnet_items(created_at DESC, updated_at DESC)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_magnet_items_name_nocase "
            "ON magnet_items(name COLLATE NOCASE)"
        )
        sync_statuses = ", ".join(f"'{status}'" for status in SYNC_ACTIVE_STATUSES)
        desired = f"WHERE status IN ({sync_statuses})"
        existing = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_magnet_items_sync'"
        ).fetchone()
        # 活跃状态集合变了就重建：部分索引的谓词是写死的，旧的不能复用于新查询。
        if existing is not None and desired not in (existing[0] or ""):
            db.execute("DROP INDEX idx_magnet_items_sync")
        db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_magnet_items_sync ON magnet_items(hash) {desired}"
        )

    # 采集中心新增列：旧库升级时补列，不重建表、不丢数据。
    _ADDED_COLUMNS = (
        ("detail_url", "TEXT DEFAULT ''"),
        ("batch_id", "TEXT DEFAULT ''"),
        ("source_kind", "TEXT DEFAULT ''"),
    )

    @classmethod
    def _migrate_columns(cls, db: sqlite3.Connection) -> None:
        existing = {row[1] for row in db.execute("PRAGMA table_info(magnet_items)")}
        for name, ddl in cls._ADDED_COLUMNS:
            if name not in existing:
                db.execute(f"ALTER TABLE magnet_items ADD COLUMN {name} {ddl}")

    @staticmethod
    def _item_to_row(item: MagnetItem) -> dict:
        return {
            "hash": item.hash,
            "name": item.name,
            "magnet": item.magnet,
            "size": item.size or "",
            "source_url": item.source_url,
            "detail_url": item.detail_url or "",
            "batch_id": item.batch_id or "",
            "source_kind": item.source_kind or "",
            "category": item.category or "",
            "save_path": item.save_path or "",
            "status": item.status.value,
            "progress": item.progress,
            "torrent_state": item.torrent_state or "",
            "error_msg": item.error_msg or "",
            "created_at": iso_utc(item.created_at),
            "updated_at": iso_utc(item.updated_at),
        }

    @staticmethod
    def _row_to_item(row: sqlite3.Row | None) -> MagnetItem | None:
        if row is None:
            return None
        d: dict = dict(row)
        try:
            for field_name in (
                "category",
                "save_path",
                "torrent_state",
                "error_msg",
                "size",
                "detail_url",
                "batch_id",
            ):
                if field_name in d and d[field_name] == "":
                    d[field_name] = None
            # 时间列：老行是 naive（当年容器 TZ=UTC，按 UTC 解释），新行带 +00:00。
            # 解析不了的脏值不丢整行——丢掉等于用户看不见、同步也修不回来。
            for field_name in ("created_at", "updated_at"):
                parsed = parse_iso_utc(d.get(field_name))
                if parsed is not None:
                    d[field_name] = parsed
                else:
                    d.pop(field_name, None)
            return MagnetItem(**d)
        except (ValidationError, ValueError, TypeError) as e:
            hash_val = d.get("hash", "unknown")
            log.error("sqlite 行反序列化失败 hash=%s: %s", hash_val, e)
            return None

    @classmethod
    def _write_updated_fields(
        cls,
        db: sqlite3.Connection,
        current_hash: str,
        item: MagnetItem,
        fields: dict,
        expected_statuses: set[TaskStatus] | None = None,
    ) -> bool:
        serialized = cls._item_to_row(item)
        params: dict[str, object] = {"current_hash": current_hash}
        assignments: list[str] = []
        for column in dict.fromkeys([*fields, "updated_at"]):
            parameter = f"value_{column}"
            assignments.append(f"{column} = :{parameter}")
            params[parameter] = serialized[column]

        status_guard = ""
        if expected_statuses is not None:
            if not expected_statuses:
                return False
            names = []
            for index, status in enumerate(
                sorted(expected_statuses, key=lambda value: value.value)
            ):
                name = f"expected_status_{index}"
                names.append(f":{name}")
                params[name] = status.value
            status_guard = f" AND status IN ({', '.join(names)})"

        cursor = db.execute(
            f"UPDATE magnet_items SET {', '.join(assignments)} "
            f"WHERE hash = :current_hash{status_guard}",
            params,
        )
        db.commit()
        return cursor.rowcount > 0

    # ── 核心操作 ──────────────────────────────

    def add(self, item: MagnetItem) -> bool:
        with self._lock, self._connect() as db:
            row = self._item_to_row(item)
            try:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO magnet_items
                       (hash, name, magnet, size, source_url, detail_url, batch_id, source_kind,
                        category, save_path, status, progress, torrent_state, error_msg,
                        created_at, updated_at)
                       VALUES (:hash, :name, :magnet, :size, :source_url, :detail_url, :batch_id,
                        :source_kind, :category, :save_path, :status, :progress, :torrent_state,
                        :error_msg, :created_at, :updated_at)""",
                    row,
                )
                db.commit()
                return cursor.rowcount > 0
            except sqlite3.OperationalError:
                # 写不进去（库被锁/磁盘/IO）不等于「这条已经存在」：返回 False 会被上报入口
                # 当成 duplicate，条目静默消失、用户还收到「已入库」。这里抛出去，让调用方
                # 走失败路径（HTTP 5xx → Mac 代理会重发），下一轮重试还能补上。
                log.error("sqlite: add(%s) 写入失败，未入库", item.hash[:16], exc_info=True)
                raise
            except (TypeError, ValueError) as e:
                log.error("sqlite: add(%s) 数据序列化错误: %s", item.hash[:16], e, exc_info=True)
                raise

    def get(self, hash_key: str) -> Optional[MagnetItem]:
        with self._lock, self._connect() as db:
            cursor = db.execute("SELECT * FROM magnet_items WHERE hash = ?", (hash_key,))
            return self._row_to_item(cursor.fetchone())

    def get_many(self, hashes: Iterable[str]) -> Dict[str, MagnetItem]:
        """一次连接批量取条目；超过 SQLite 变量上限时按 500 分块。"""
        unique = list(dict.fromkeys(hashes))
        if not unique:
            return {}
        found: Dict[str, MagnetItem] = {}
        with self._lock, self._connect() as db:
            for start in range(0, len(unique), 500):
                chunk = unique[start : start + 500]
                placeholders = ", ".join("?" * len(chunk))
                cursor = db.execute(
                    f"SELECT * FROM magnet_items WHERE hash IN ({placeholders})",
                    chunk,
                )
                for row in cursor.fetchall():
                    item = self._row_to_item(row)
                    if item is not None:
                        found[item.hash] = item
        return found

    def update(self, hash_key: str, **fields) -> Optional[MagnetItem]:
        known = MagnetItem.model_fields
        unknown = [k for k in fields if k not in known]
        if unknown:
            return None

        with self._lock, self._connect() as db:
            # 读取用于模型校验；SQL 仅写入请求字段，避免覆盖其他连接的无关修改。
            cursor = db.execute("SELECT * FROM magnet_items WHERE hash = ?", (hash_key,))
            row = cursor.fetchone()
            if row is None:
                return None
            item = self._row_to_item(row)
            if item is None:
                return None

            new_item = _validated_update(item, fields)
            if new_item is None:
                return None
            if not self._write_updated_fields(db, hash_key, new_item, fields):
                return None
            return new_item

    def update_if_status(
        self,
        hash_key: str,
        expected_statuses: set[TaskStatus],
        **fields,
    ) -> Optional[MagnetItem]:
        """仅当当前状态匹配时更新；SQL 条件保证比较与写入原子化。"""
        if not expected_statuses:
            return None
        unknown = [key for key in fields if key not in MagnetItem.model_fields]
        if unknown:
            return None

        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM magnet_items WHERE hash = ?",
                (hash_key,),
            ).fetchone()
            item = self._row_to_item(row)
            if item is None or item.status not in expected_statuses:
                return None

            new_item = _validated_update(item, fields)
            if new_item is None:
                return None
            if not self._write_updated_fields(
                db,
                hash_key,
                new_item,
                fields,
                expected_statuses,
            ):
                return None
            return new_item

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
            if isinstance(status, (list, set, frozenset, tuple)):
                if not status:
                    return []
                placeholders = ", ".join(["?"] * len(status))
                conditions.append(f"status IN ({placeholders})")
                params.extend(status)
            else:
                conditions.append("status = ?")
                params.append(status)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM magnet_items {where} ORDER BY name COLLATE NOCASE ASC LIMIT ?"
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

    def recent(self, limit: int = 10) -> List[MagnetItem]:
        if limit <= 0:
            return []
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "SELECT * FROM magnet_items ORDER BY created_at DESC, updated_at DESC LIMIT ?",
                (limit,),
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

    def list_for_sync(self, after_hash: Optional[str] = None, limit: int = 500) -> List[MagnetItem]:
        """按 hash 顺序取一页活跃条目；keyset 分页，不重扫头部、不排序 name。"""
        if limit <= 0:
            return []
        # 部分索引的谓词必须是字面量，绑定参数会让 SQLite 无法证明索引可用。
        status_list = ", ".join(f"'{status}'" for status in SYNC_ACTIVE_STATUSES)
        where = f"status IN ({status_list})"
        params: list[str | int] = []
        if after_hash:
            where += " AND hash > ?"
            params.append(after_hash)
        params.append(limit)
        with self._lock, self._connect() as db:
            cursor = db.execute(_SYNC_PAGE_SQL.format(where=where), params)
            return [item for r in cursor.fetchall() if (item := self._row_to_item(r)) is not None]

    # ── 统计 ──────────────────────────────

    @property
    def count(self) -> int:
        with self._lock, self._connect() as db:
            cursor = db.execute("SELECT COUNT(*) FROM magnet_items")
            row = cursor.fetchone()
            return row[0] if row else 0

    def count_and_page(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "name",
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
            order_by = (
                "created_at DESC, updated_at DESC"
                if order == "newest"
                else "name COLLATE NOCASE ASC"
            )
            cursor = db.execute(
                f"SELECT * FROM magnet_items {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            items = [item for r in cursor.fetchall() if (item := self._row_to_item(r)) is not None]

        return total, items

    def stats(self) -> StoreStats:
        s = StoreStats()
        with self._lock, self._connect() as db:
            # By category
            cursor = db.execute(
                "SELECT CASE WHEN category IS NULL OR category = '' THEN '未分类' ELSE category END as cat, "
                "COUNT(*) as cnt FROM magnet_items GROUP BY cat"
            )
            for row in cursor.fetchall():
                s.by_category[row["cat"]] = row["cnt"]

            # By status：total 由各状态计数相加，不再单独全表 COUNT(*)
            cursor = db.execute("SELECT status, COUNT(*) as cnt FROM magnet_items GROUP BY status")
            for row in cursor.fetchall():
                s.by_status[row["status"]] = row["cnt"]
            s.total = sum(s.by_status.values())

            # Pending count
            cursor = db.execute(
                "SELECT COUNT(*) FROM magnet_items WHERE status = ?",
                (TaskStatus.pending.value,),
            )
            row = cursor.fetchone()
            s.pending_count = row[0] if row else 0

        return s

    def clear(self) -> int:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT COUNT(*) FROM magnet_items").fetchone()
            count = row[0] if row else 0
            db.execute("DELETE FROM magnet_items")
            db.commit()
        return count
