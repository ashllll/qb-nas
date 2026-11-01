"""采集任务与上报事件的持久化。

两张表，和资源库共用一个 SQLite 文件（``STORE_DB_PATH``）：

- ``ingest_events``：Mac 上报事件的幂等表。同一 ``event_id`` 重发只处理一次，
  并原样返回第一次的结果（accepted/duplicate/rejected）。
- ``crawl_jobs``：一个网址 = 一个任务。任务状态落盘，服务重启后未完成的任务
  会被重新排上（见 ``JobStore.claim_resumable``），所以「没干完的活能接着做」。

时间戳沿用项目不变量：naive-local ``datetime.now()`` ISO 字符串，字典序即时间序。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


class JobStatus:
    """抓取任务状态。"""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"

    ALL = (queued, running, done, failed)
    ACTIVE = (queued, running)


# 失败原因代码 → 给用户看的中文。代码本身留在 reason 里给机器判读，UI 只显示这句话。
REASON_LABELS = {
    "no_magnet": "页面里没有磁力",
    "blocked": "被站点挡住",
    "timeout": "抓取超时",
    "http_error": "页面请求失败",
    "parse_error": "页面解析失败",
    "unsafe_url": "网址不安全",
    "browser_unavailable": "浏览器不可用",
    "crawl_error": "抓取出错",
}


def reason_label(reason: str | None, message: str = "") -> str:
    """把原因代码翻成人话；已经是人话（原始消息）就原样返回。"""
    text = (reason or "").strip()
    if not text:
        return (message or "").strip()
    label = REASON_LABELS.get(text)
    if label:
        return label
    return text


@dataclass
class CrawlJob:
    """一次「网址 → 磁力」抓取任务。"""

    job_id: str
    url: str
    batch_id: str = ""
    depth: int = 1
    status: str = JobStatus.queued
    source: str = "mac"
    reason: str = ""
    message: str = ""
    found: int = 0
    added: int = 0
    duplicates: int = 0
    pages: int = 0
    browser_used: bool = False
    first_item_ms: Optional[int] = None
    elapsed_ms: Optional[int] = None
    errors: List[dict] = field(default_factory=list)
    attempts: int = 0
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    updated_at: str = ""

    @property
    def reason_label(self) -> str:
        message = self.errors[-1]["msg"] if self.errors else ""
        return reason_label(self.reason, message)

    @property
    def urls_in_batch(self) -> int:
        """这一批里有几个网址（当前一次上报一个网址，留作多条上报的接口）。"""
        return 1

    @property
    def summary(self) -> str:
        """给用户看的一句话：「从 1 个网址收到 8 条」。"""
        if self.status == JobStatus.done:
            if self.added:
                text = f"从 {self.urls_in_batch} 个网址收到 {self.added} 条"
                if self.duplicates:
                    text += f"（{self.duplicates} 条库里已有）"
                return text
            if self.found:
                return f"从 {self.urls_in_batch} 个网址找到 {self.found} 条，全部已在库里"
            label = self.reason_label
            return "没有找到磁力" + (f"：{label}" if label else "")
        if self.status == JobStatus.failed:
            label = self.reason_label
            return "抓取失败" + (f"：{label}" if label else "")
        if self.status == JobStatus.running:
            return f"正在找磁力…已收到 {self.added} 条"
        return "等待抓取"

    def as_dict(self) -> dict:
        data = {
            "job_id": self.job_id,
            "batch_id": self.batch_id,
            "url": self.url,
            "depth": self.depth,
            "status": self.status,
            "source": self.source,
            "reason": self.reason,
            "reason_label": self.reason_label,
            "message": self.message,
            "found": self.found,
            "added": self.added,
            "duplicates": self.duplicates,
            "pages": self.pages,
            "browser_used": self.browser_used,
            "first_item_ms": self.first_item_ms,
            "elapsed_ms": self.elapsed_ms,
            "errors": list(self.errors),
            "attempts": self.attempts,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }
        data["summary"] = self.summary
        return data


@dataclass
class IngestRecord:
    """一条上报事件的处理结果（幂等键 event_id）。"""

    event_id: str
    kind: str
    value_hash: str
    status: str
    item_id: str = ""
    job_id: str = ""
    reason: str = ""
    created_at: str = ""

    def as_response(self) -> dict:
        return {
            "ok": self.status != "rejected",
            "status": self.status,
            "item_id": self.item_id or None,
            "job_id": self.job_id or None,
            "reason": self.reason or "",
            "event_id": self.event_id,
        }


def _now() -> str:
    return datetime.now().isoformat()


_JOB_COLUMNS = (
    "job_id",
    "batch_id",
    "url",
    "depth",
    "status",
    "source",
    "reason",
    "message",
    "found",
    "added",
    "duplicates",
    "pages",
    "browser_used",
    "first_item_ms",
    "elapsed_ms",
    "errors",
    "attempts",
    "created_at",
    "started_at",
    "finished_at",
    "updated_at",
)


class JobStore:
    """crawl_jobs + ingest_events 的 SQLite 适配器（线程安全，短连接）。"""

    # 常驻服务会一直往里写：留存上限够回溯，又不让库无限长。
    MAX_EVENTS = 5000
    MAX_JOBS = 500
    _PRUNE_EVERY = 200

    def __init__(self, db_path: str | Path = "data/magnet_items.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._writes_since_prune = 0
        self._init_db()

    # ── 连接 ──────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), isolation_level="DEFERRED")
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            raise
        return closing(conn)

    def _init_db(self) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_jobs (
                    job_id TEXT PRIMARY KEY,
                    batch_id TEXT DEFAULT '',
                    url TEXT NOT NULL,
                    depth INTEGER DEFAULT 1,
                    status TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    message TEXT DEFAULT '',
                    found INTEGER DEFAULT 0,
                    added INTEGER DEFAULT 0,
                    duplicates INTEGER DEFAULT 0,
                    pages INTEGER DEFAULT 0,
                    browser_used INTEGER DEFAULT 0,
                    first_item_ms INTEGER,
                    elapsed_ms INTEGER,
                    errors TEXT DEFAULT '[]',
                    attempts INTEGER DEFAULT 0,
                    created_at TEXT,
                    started_at TEXT DEFAULT '',
                    finished_at TEXT DEFAULT '',
                    updated_at TEXT
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS ingest_events (
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    value_hash TEXT DEFAULT '',
                    status TEXT NOT NULL,
                    item_id TEXT DEFAULT '',
                    job_id TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    created_at TEXT
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_crawl_jobs_created ON crawl_jobs(created_at DESC)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status ON crawl_jobs(status)")
            db.commit()

    # ── crawl_jobs ────────────────────────

    def create(self, job: CrawlJob) -> CrawlJob:
        now = _now()
        job.created_at = job.created_at or now
        job.updated_at = now
        with self._lock, self._connect() as db:
            db.execute(
                f"INSERT OR REPLACE INTO crawl_jobs ({', '.join(_JOB_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in _JOB_COLUMNS)})",
                self._row_from_job(job),
            )
            db.commit()
        return job

    def get(self, job_id: str) -> Optional[CrawlJob]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM crawl_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_from_row(row)

    def update(self, job_id: str, **fields) -> Optional[CrawlJob]:
        if not fields:
            return self.get(job_id)
        unknown = [k for k in fields if k not in _JOB_COLUMNS]
        if unknown:
            raise ValueError(f"未知的任务字段: {unknown}")
        params = dict(self._coerce_fields(fields))
        params["job_id"] = job_id
        params["updated_at"] = _now()
        assignments = ", ".join(f"{name} = :{name}" for name in params if name != "job_id")
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE crawl_jobs SET {assignments} WHERE job_id = :job_id", params)
            db.commit()
        return self.get(job_id)

    def list(self, limit: int = 20, status: Optional[str] = None) -> List[CrawlJob]:
        limit = max(0, min(int(limit), 200))
        if limit == 0:
            return []
        sql = "SELECT * FROM crawl_jobs"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [job for row in rows if (job := self._job_from_row(row)) is not None]

    def find_active_by_url(self, url: str) -> Optional[CrawlJob]:
        """同一个网址已有排队/进行中的任务时复用它，避免重复抓取。"""
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM crawl_jobs WHERE url = ? AND status IN (?, ?) "
                "ORDER BY created_at DESC LIMIT 1",
                (url, JobStatus.queued, JobStatus.running),
            ).fetchone()
        return self._job_from_row(row)

    def claim_resumable(self, max_attempts: int = 2) -> List[str]:
        """服务启动时调用：把中断的（queued/running）任务重新排上。

        running 状态意味着上次是被强杀/崩溃打断的——回到 queued 并计入一次尝试，
        超过 ``max_attempts`` 的任务不再自动重试，改为失败并留下原因。
        """
        resumed: List[str] = []
        now = _now()
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM crawl_jobs WHERE status IN (?, ?) ORDER BY created_at ASC",
                JobStatus.ACTIVE,
            ).fetchall()
            for row in rows:
                job = self._job_from_row(row)
                if job is None:
                    continue
                if job.attempts >= max_attempts:
                    db.execute(
                        "UPDATE crawl_jobs SET status=?, reason=?, message=?, finished_at=?, "
                        "updated_at=? WHERE job_id=?",
                        (
                            JobStatus.failed,
                            "服务重启多次仍未完成，已停止自动重试",
                            "需要手动重试",
                            now,
                            now,
                            job.job_id,
                        ),
                    )
                    continue
                db.execute(
                    "UPDATE crawl_jobs SET status=?, attempts=attempts+1, message=?, "
                    "updated_at=? WHERE job_id=?",
                    (JobStatus.queued, "服务重启后继续", now, job.job_id),
                )
                resumed.append(job.job_id)
            db.commit()
        return resumed

    def counts(self) -> dict:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT status, COUNT(*) FROM crawl_jobs GROUP BY status").fetchall()
        by_status = {row[0]: row[1] for row in rows}
        return {
            "total": sum(by_status.values()),
            "queued": by_status.get(JobStatus.queued, 0),
            "running": by_status.get(JobStatus.running, 0),
            "done": by_status.get(JobStatus.done, 0),
            "failed": by_status.get(JobStatus.failed, 0),
        }

    # ── ingest_events ─────────────────────

    def get_event(self, event_id: str) -> Optional[IngestRecord]:
        if not event_id:
            return None
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM ingest_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        return IngestRecord(
            event_id=row["event_id"],
            kind=row["kind"],
            value_hash=row["value_hash"],
            status=row["status"],
            item_id=row["item_id"],
            job_id=row["job_id"],
            reason=row["reason"],
            created_at=row["created_at"],
        )

    def record_event(
        self,
        *,
        event_id: str,
        kind: str,
        value_hash: str,
        status: str,
        item_id: str = "",
        job_id: str = "",
        reason: str = "",
    ) -> IngestRecord:
        record = IngestRecord(
            event_id=event_id,
            kind=kind,
            value_hash=value_hash,
            status=status,
            item_id=item_id,
            job_id=job_id,
            reason=reason,
            created_at=_now(),
        )
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO ingest_events "
                "(event_id, kind, value_hash, status, item_id, job_id, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.event_id,
                    record.kind,
                    record.value_hash,
                    record.status,
                    record.item_id,
                    record.job_id,
                    record.reason,
                    record.created_at,
                ),
            )
            db.commit()
        self._maybe_prune()
        return record

    def _maybe_prune(self) -> None:
        self._writes_since_prune += 1
        if self._writes_since_prune < self._PRUNE_EVERY:
            return
        self._writes_since_prune = 0
        try:
            self.prune()
        except Exception:
            log.warning("清理历史事件/任务失败", exc_info=True)

    def prune(self) -> None:
        """只留最近的 N 条上报事件和 N 个任务（常驻服务的表需要上限）。"""
        with self._lock, self._connect() as db:
            db.execute(
                "DELETE FROM ingest_events WHERE event_id NOT IN "
                "(SELECT event_id FROM ingest_events ORDER BY created_at DESC LIMIT ?)",
                (self.MAX_EVENTS,),
            )
            db.execute(
                "DELETE FROM crawl_jobs WHERE job_id NOT IN "
                "(SELECT job_id FROM crawl_jobs ORDER BY created_at DESC LIMIT ?)",
                (self.MAX_JOBS,),
            )
            db.commit()

    def last_event_at(self) -> Optional[str]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT MAX(created_at) FROM ingest_events").fetchone()
        return row[0] if row else None

    def recent_events(self, limit: int = 10) -> List[IngestRecord]:
        limit = max(0, min(int(limit), 100))
        if limit == 0:
            return []
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM ingest_events ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            IngestRecord(
                event_id=row["event_id"],
                kind=row["kind"],
                value_hash=row["value_hash"],
                status=row["status"],
                item_id=row["item_id"],
                job_id=row["job_id"],
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ── 行 ↔ 对象 ─────────────────────────

    @staticmethod
    def _coerce_fields(fields: dict) -> dict:
        coerced = dict(fields)
        if "errors" in coerced:
            coerced["errors"] = json.dumps(coerced["errors"], ensure_ascii=False)
        if "browser_used" in coerced:
            coerced["browser_used"] = 1 if coerced["browser_used"] else 0
        return coerced

    @classmethod
    def _row_from_job(cls, job: CrawlJob) -> dict:
        return {
            "job_id": job.job_id,
            "batch_id": job.batch_id,
            "url": job.url,
            "depth": job.depth,
            "status": job.status,
            "source": job.source,
            "reason": job.reason,
            "message": job.message,
            "found": job.found,
            "added": job.added,
            "duplicates": job.duplicates,
            "pages": job.pages,
            "browser_used": 1 if job.browser_used else 0,
            "first_item_ms": job.first_item_ms,
            "elapsed_ms": job.elapsed_ms,
            "errors": json.dumps(job.errors, ensure_ascii=False),
            "attempts": job.attempts,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "updated_at": job.updated_at,
        }

    @classmethod
    def _job_from_row(cls, row: sqlite3.Row | None) -> Optional[CrawlJob]:
        if row is None:
            return None
        try:
            errors = json.loads(row["errors"] or "[]")
        except (TypeError, ValueError):
            errors = []
        return CrawlJob(
            job_id=row["job_id"],
            url=row["url"],
            batch_id=row["batch_id"] or "",
            depth=row["depth"] or 1,
            status=row["status"],
            source=row["source"] or "",
            reason=row["reason"] or "",
            message=row["message"] or "",
            found=row["found"] or 0,
            added=row["added"] or 0,
            duplicates=row["duplicates"] or 0,
            pages=row["pages"] or 0,
            browser_used=bool(row["browser_used"]),
            first_item_ms=row["first_item_ms"],
            elapsed_ms=row["elapsed_ms"],
            errors=errors if isinstance(errors, list) else [],
            attempts=row["attempts"] or 0,
            created_at=row["created_at"] or "",
            started_at=row["started_at"] or "",
            finished_at=row["finished_at"] or "",
            updated_at=row["updated_at"] or "",
        )
