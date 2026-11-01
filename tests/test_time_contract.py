"""时间戳契约（《约定》第 1 条）：进库/进响应的时间一律 UTC 且带时区。

- 新写入：``MagnetItem`` / ``crawl_jobs`` 的时间是 UTC，ISO 字符串带 ``+00:00``；
- 历史行：naive 值按 UTC 解释（实测 8899 的 created_at "2026-11-01T08:13:36.898188"
  对应本地 16:24+08:00）；
- 脏时间戳不许把整行丢掉（丢了用户看不见、同步也修不回来）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from magnet_harvester.bus import MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.jobs import JobStore
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.services.ingest import IngestService
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore, SQLiteItemStore
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions
from magnet_harvester.utils.timeutil import iso_utc, parse_iso_utc, to_utc, utc_now

LEGACY_NAIVE = "2026-11-01T08:13:36.898188"


class NoopCrawler:
    max_depth = 2

    async def crawl(self, url: str, depth: int = 1):
        yield {"type": "done", "total": 0, "url": url, "metrics": {"pages_crawled": 1}}


def _item(hash_key: str = "A" * 40) -> MagnetItem:
    return MagnetItem(
        hash=hash_key,
        name="time-item",
        magnet=f"magnet:?xt=urn:btih:{hash_key}",
    )


def _insert_raw_row(db_path, **overrides) -> None:
    row = {
        "hash": "B" * 40,
        "name": "legacy",
        "magnet": f"magnet:?xt=urn:btih:{'B' * 40}",
        "size": "",
        "source_url": "",
        "detail_url": "",
        "batch_id": "",
        "source_kind": "",
        "category": "电影",
        "save_path": "",
        "status": TaskStatus.error.value,
        "progress": 0.0,
        "torrent_state": "stoppedDL",
        "error_msg": "旧数据",
        "created_at": LEGACY_NAIVE,
        "updated_at": LEGACY_NAIVE,
    }
    row.update(overrides)
    with sqlite3.connect(str(db_path)) as db:
        db.execute(
            f"INSERT INTO magnet_items ({', '.join(row)}) "
            f"VALUES ({', '.join('?' for _ in row)})",
            list(row.values()),
        )
        db.commit()


# ── 工具函数本身 ──────────────────────────────


def test_utc_now_is_aware_and_zero_offset():
    now = utc_now()
    assert now.tzinfo is timezone.utc
    assert now.utcoffset().total_seconds() == 0


def test_naive_is_interpreted_as_utc():
    parsed = parse_iso_utc(LEGACY_NAIVE)
    assert parsed is not None
    assert parsed.tzinfo is timezone.utc
    assert parsed.hour == 8
    assert iso_utc(parsed) == "2026-11-01T08:13:36.898188+00:00"


def test_non_utc_offset_is_converted_to_utc():
    parsed = parse_iso_utc("2026-11-01T16:13:36.898188+08:00")
    assert parsed is not None
    assert iso_utc(parsed) == "2026-11-01T08:13:36.898188+00:00"
    assert to_utc(datetime(2026, 11, 1, 16, 13)).hour == 16  # naive 视为 UTC


def test_unparseable_timestamp_returns_none():
    assert parse_iso_utc("not-a-date") is None
    assert parse_iso_utc("") is None
    assert parse_iso_utc(None) is None


# ── 模型 ────────────────────────────────────


def test_new_item_timestamps_are_utc():
    item = _item()
    assert item.created_at.tzinfo is timezone.utc
    assert item.updated_at.tzinfo is timezone.utc
    assert item.created_at.isoformat().endswith("+00:00")


def test_naive_input_is_normalized_not_stored_naive():
    item = MagnetItem(
        hash="C" * 40,
        name="naive",
        magnet=f"magnet:?xt=urn:btih:{'C' * 40}",
        created_at=datetime(2026, 11, 1, 8, 13, 36),
    )
    assert item.created_at.tzinfo is timezone.utc
    assert item.created_at.isoformat().endswith("+00:00")


# ── SQLite 落盘与读回 ──────────────────────────


def test_sqlite_write_uses_offset_and_roundtrip_keeps_utc(tmp_path):
    db_path = tmp_path / "items.db"
    backend = SQLiteItemStore(db_path)
    backend.add(_item())

    with sqlite3.connect(str(db_path)) as db:
        created_at, updated_at = db.execute(
            "SELECT created_at, updated_at FROM magnet_items WHERE hash = ?", ("A" * 40,)
        ).fetchone()
    assert created_at.endswith("+00:00"), created_at
    assert updated_at.endswith("+00:00"), updated_at

    loaded = backend.get("A" * 40)
    assert loaded is not None
    assert loaded.created_at.tzinfo is not None
    assert loaded.created_at.utcoffset().total_seconds() == 0


def test_legacy_naive_row_is_read_as_utc(tmp_path):
    db_path = tmp_path / "items.db"
    backend = SQLiteItemStore(db_path)
    _insert_raw_row(db_path)

    item = backend.get("B" * 40)
    assert item is not None, "历史 naive 行不能被丢掉"
    assert item.created_at.isoformat() == "2026-11-01T08:13:36.898188+00:00"


def test_corrupt_timestamp_row_is_still_visible(tmp_path):
    db_path = tmp_path / "items.db"
    backend = SQLiteItemStore(db_path)
    _insert_raw_row(db_path, created_at="garbage", updated_at="")

    item = backend.get("B" * 40)
    assert item is not None, "时间戳脏了也不能把整行从列表里弄没"
    assert item.status == TaskStatus.error
    assert item.created_at.tzinfo is not None


# ── 任务（crawl_jobs）与入库任务的时间 ─────────────────


@pytest.mark.asyncio
async def test_job_store_timestamps_are_utc(tmp_path):
    jobs = JobStore(tmp_path / "jobs.db")
    from magnet_harvester.jobs import CrawlJob

    job = jobs.create(CrawlJob(job_id="job-1", url="https://example.com/x"))
    assert job.created_at.endswith("+00:00")
    assert job.updated_at.endswith("+00:00")

    stored = jobs.get("job-1")
    assert stored is not None
    assert parse_iso_utc(stored.created_at) is not None


@pytest.mark.asyncio
async def test_ingest_job_started_and_finished_are_utc(tmp_path):
    store = AsyncItemStore(InMemoryItemStore())
    bus = MessageBus()
    jobs = JobStore(tmp_path / "jobs.db")
    service = IngestService(
        store=store,
        jobs=jobs,
        classifier=LocalClassifier(),
        crawler=NoopCrawler(),
        bus=bus,
        discovery=DiscoveryTransitions(store=store, bus=bus),
        classification=ClassificationTransitions(store=store, bus=bus),
    )

    result = await service.ingest_url("https://example.com/torrents/search/4/sexart", auto_start=False)
    assert result["status"] == "accepted"
    job_id = result["job_id"]
    await service._run_job(job_id)  # 直接跑一遍，不依赖后台调度时序
    job = jobs.get(job_id)
    assert job is not None and job.status == "done"
    assert job.started_at.endswith("+00:00"), job.started_at
    assert job.finished_at.endswith("+00:00"), job.finished_at
    assert parse_iso_utc(job.started_at) is not None
