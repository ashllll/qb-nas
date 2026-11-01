"""任务表清理不许删掉还在跑/还在排队的任务。

``prune()`` 只按「最新 N 条」保留，不看状态。常驻机器上任务表迟早超过上限
（每粘一个网址就是一个任务），于是一个还在 queued/running 的老任务会被删掉：

- 它在 /api/v1/jobs 里消失，进度和失败原因都看不到了；
- ``find_active_by_url`` 查不到它 → 同一个网址再粘一次会再建一个任务，
  「同一网址复用同一任务」的承诺失效（重复抓取）；
- 跑完时 ``update()`` 找不到行，任务的结束状态直接丢掉。
"""

from __future__ import annotations

from magnet_harvester.jobs import CrawlJob, JobStatus, JobStore

MAX_JOBS = JobStore.MAX_JOBS


def job(job_id: str, status: JobStatus, url: str) -> CrawlJob:
    return CrawlJob(job_id=job_id, batch_id="b", url=url, depth=1, status=status, source="mac")


def test_prune_keeps_active_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.create(job("long-running", JobStatus.running, "http://example.com/slow"))
    store.create(job("waiting", JobStatus.queued, "http://example.com/queued"))
    for i in range(MAX_JOBS + 10):
        store.create(job(f"done-{i}", JobStatus.done, f"http://example.com/{i}"))

    store.prune()

    assert store.get("long-running") is not None, "进行中的任务被清理掉了"
    assert store.get("waiting") is not None, "排队中的任务被清理掉了"
    assert store.get("done-0") is None, "历史任务仍然要按上限清理"


def test_pruned_url_can_still_be_deduplicated(tmp_path):
    """清理之后，同一网址仍然要能认回正在跑的那个任务。"""
    store = JobStore(tmp_path / "jobs.db")
    store.create(job("long-running", JobStatus.running, "http://example.com/slow"))
    for i in range(MAX_JOBS + 10):
        store.create(job(f"done-{i}", JobStatus.done, f"http://example.com/{i}"))

    store.prune()

    active = store.find_active_by_url("http://example.com/slow")
    assert active is not None and active.job_id == "long-running"


def test_stuck_queued_job_is_eventually_pruned(tmp_path):
    """豁免清理不能变成无限增长：排队超过保留期还没跑的按死任务清掉。"""
    from datetime import timedelta

    from magnet_harvester.utils.timeutil import iso_utc, utc_now

    store = JobStore(tmp_path / "jobs.db")
    stale = job("stale", JobStatus.queued, "http://example.com/stale")
    stale.created_at = iso_utc(utc_now() - timedelta(days=JobStore.QUEUED_RETENTION_DAYS + 1))
    store.create(stale)
    store.create(job("fresh", JobStatus.queued, "http://example.com/fresh"))

    store.prune()

    assert store.get("stale") is None, "过期排队任务要清掉"
    assert store.get("fresh") is not None, "刚排队的任务不许动"
