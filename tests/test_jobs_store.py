"""抓取任务与上报事件的持久化：重启后还认得「没干完的活」。"""

from __future__ import annotations

from magnet_harvester.jobs import CrawlJob, JobStatus, JobStore


def _job(job_id: str = "job-1", url: str = "https://example.com/list", **kwargs) -> CrawlJob:
    return CrawlJob(job_id=job_id, batch_id="batch-1", url=url, depth=2, **kwargs)


def test_job_roundtrip_keeps_counters_and_errors(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.create(_job())

    updated = store.update(
        "job-1",
        status=JobStatus.running,
        found=3,
        added=2,
        duplicates=1,
        pages=2,
        browser_used=True,
        errors=[{"msg": "详情页 403", "url": "https://example.com/d/1", "reason": "blocked"}],
    )

    assert updated is not None
    assert updated.found == 3
    assert updated.added == 2
    assert updated.browser_used is True
    assert updated.errors[0]["reason"] == "blocked"
    fetched = store.get("job-1")
    assert fetched is not None and fetched.pages == 2
    assert "已收到 2 条" in fetched.summary

    done = store.update("job-1", status=JobStatus.done)
    assert done is not None
    assert done.summary == "从 1 个网址收到 2 条（1 条库里已有）"


def test_summary_explains_empty_result_and_failure():
    """原因代码翻成人话给用户看，代码本身仍留在 reason 里给机器判读。"""
    done_empty = _job(status=JobStatus.done, reason="no_magnet")
    assert done_empty.summary == "没有找到磁力：页面里没有磁力"
    assert done_empty.reason == "no_magnet"
    assert done_empty.as_dict()["reason_label"] == "页面里没有磁力"

    blocked = _job(status=JobStatus.failed, reason="blocked")
    assert blocked.summary == "抓取失败：被站点挡住"

    failed = _job(status=JobStatus.failed, reason="connect timed out")
    assert failed.summary == "抓取失败：connect timed out"

    running = _job(status=JobStatus.running, added=4)
    assert "已收到 4 条" in running.summary


def test_list_returns_newest_first(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.create(_job("job-1"))
    store.create(_job("job-2"))

    jobs = store.list(limit=10)

    assert [job.job_id for job in jobs][0] == "job-2"


def test_claim_resumable_requeues_interrupted_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.create(_job("job-running", status=JobStatus.running, attempts=0))
    store.create(_job("job-queued", status=JobStatus.queued, attempts=0))
    store.create(_job("job-done", status=JobStatus.done, attempts=1))

    resumed = store.claim_resumable(max_attempts=2)

    assert sorted(resumed) == ["job-queued", "job-running"]
    running = store.get("job-running")
    assert running is not None and running.status == JobStatus.queued
    assert running.attempts == 1
    assert running.message == "服务重启后继续"


def test_claim_resumable_gives_up_after_max_attempts(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.create(_job("job-fire", status=JobStatus.running, attempts=2))

    resumed = store.claim_resumable(max_attempts=2)

    assert resumed == []
    fired = store.get("job-fire")
    assert fired is not None and fired.status == JobStatus.failed
    assert "重启多次" in fired.reason


def test_find_active_by_url_reuses_running_job(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.create(_job("job-1", url="https://example.com/list", status=JobStatus.running))
    store.create(_job("job-2", url="https://example.com/other", status=JobStatus.done))

    assert store.find_active_by_url("https://example.com/list").job_id == "job-1"
    assert store.find_active_by_url("https://example.com/other") is None


def test_ingest_events_are_idempotent_records(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.record_event(
        event_id="evt-1",
        kind="magnet",
        value_hash="abc",
        status="accepted",
        item_id="HASH1",
    )

    record = store.get_event("evt-1")

    assert record is not None
    assert record.as_response()["status"] == "accepted"
    assert record.as_response()["item_id"] == "HASH1"
    assert store.get_event("evt-missing") is None
    assert store.last_event_at() is not None
    assert [r.event_id for r in store.recent_events(5)] == ["evt-1"]


def test_counts_by_status(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.create(_job("a", status=JobStatus.done))
    store.create(_job("b", status=JobStatus.failed))
    store.create(_job("c", status=JobStatus.running))

    counts = store.counts()

    assert counts == {"total": 3, "queued": 0, "running": 1, "done": 1, "failed": 1}


def test_prune_keeps_recent_rows_only(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.MAX_JOBS = 2
    for index in range(4):
        store.create(_job(f"job-{index}"))

    store.prune()

    remaining = {job.job_id for job in store.list(limit=10)}
    assert len(remaining) == 2
    assert "job-3" in remaining
