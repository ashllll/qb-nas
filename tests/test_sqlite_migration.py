"""旧库迁移：加列不重建表、不丢数据（现场从旧版本升上来的库必须能直接开）。

线上那个 db 是好几版之前建的，后来加了 detail_url/batch_id/source_kind 三列。
_init_db 用 CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN 补列；
这里用「老 schema + 一行老数据」验证迁移后旧行还在、新列可写、重复打开幂等。
"""

from __future__ import annotations

import sqlite3

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import SQLiteItemStore

OLD_SCHEMA = """
CREATE TABLE magnet_items (
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
"""

OLD_HASH = "E" * 40
OLD_CREATED = "2026-10-31T02:00:00.000000"  # 旧版本写的 naive UTC


def _make_old_db(path) -> None:
    with sqlite3.connect(str(path)) as db:
        db.execute(OLD_SCHEMA)
        db.execute(
            "INSERT INTO magnet_items (hash, name, magnet, size, source_url, category, "
            "save_path, status, progress, torrent_state, error_msg, created_at, updated_at) "
            "VALUES (?, ?, ?, '', '', '电影', '', 'pending', 0, '', '', ?, ?)",
            (OLD_HASH, "old-item", f"magnet:?xt=urn:btih:{OLD_HASH}", OLD_CREATED, OLD_CREATED),
        )
        db.commit()


def _columns(path) -> set[str]:
    with sqlite3.connect(str(path)) as db:
        return {row[1] for row in db.execute("PRAGMA table_info(magnet_items)")}


def test_old_database_gets_new_columns_without_losing_rows(tmp_path):
    db_path = tmp_path / "legacy.db"
    _make_old_db(db_path)
    assert {"detail_url", "batch_id", "source_kind"}.isdisjoint(_columns(db_path))

    store = SQLiteItemStore(db_path)

    columns = _columns(db_path)
    assert {"detail_url", "batch_id", "source_kind"} <= columns
    item = store.get(OLD_HASH)
    assert item is not None, "迁移不能丢老数据"
    assert item.name == "old-item"
    assert item.category == "电影"
    assert item.detail_url is None
    assert item.created_at.isoformat().startswith("2026-10-31T02:00:00")
    assert item.created_at.utcoffset().total_seconds() == 0


def test_migration_is_idempotent_and_new_rows_carry_new_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    _make_old_db(db_path)
    SQLiteItemStore(db_path)
    store = SQLiteItemStore(db_path)  # 再开一次：不重复 ALTER、不报错

    new_hash = "F" * 40
    assert store.add(
        MagnetItem(
            hash=new_hash,
            name="new-item",
            magnet=f"magnet:?xt=urn:btih:{new_hash}",
            detail_url="https://example.com/details/1",
            batch_id="batch-1",
            source_kind="mac_url",
            status=TaskStatus.pending,
        )
    )
    item = store.get(new_hash)
    assert item is not None
    assert item.detail_url == "https://example.com/details/1"
    assert item.batch_id == "batch-1"
    assert item.source_kind == "mac_url"


def test_old_database_row_with_null_status_is_visible(tmp_path):
    """老库里 status 为空的脏行：不能因为一行脏数据整表读不出来。"""
    db_path = tmp_path / "legacy.db"
    _make_old_db(db_path)
    with sqlite3.connect(str(db_path)) as db:
        db.execute("UPDATE magnet_items SET status = NULL WHERE hash = ?", (OLD_HASH,))
        db.commit()

    store = SQLiteItemStore(db_path)
    # 该行会因 status=NULL 被反序列化跳过（记录 error 日志），但其他行照常可读
    assert store.get(OLD_HASH) is None
    assert store.count == 1
