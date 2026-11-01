"""抓取任务的生命周期：不能留下「看起来还在跑」的僵尸任务。

服务重启续跑（claim_resumable）、关服务退回 queued 已经由 test_ingest_service 覆盖；
这里补的是反向：中途任何一步抛错时，``_running`` 必须被清掉，否则这个 job_id
再也不会被 start_job 重新启动，只能等下一次重启。
"""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.bus import MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.jobs import CrawlJob, JobStatus, JobStore
from magnet_harvester.services.ingest import IngestService
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions
from magnet_harvester.utils.bg_tasks import BGTaskManager

URL = "https://example.com/torrents/search/4/sexart"


class NoopCrawler:
    max_depth = 2

    async def crawl(self, url: str, depth: int = 1):
        yield {"type": "done", "total": 0, "url": url, "metrics": {}}


def build(tmp_path):
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
        task_manager=BGTaskManager(),
    )
    return service, jobs


@pytest.mark.asyncio
async def test_running_entry_is_cleared_when_job_update_fails(tmp_path):
    service, jobs = build(tmp_path)
    job = jobs.create(CrawlJob(job_id="job-1", url=URL))
    assert job.status == JobStatus.queued

    original_update = jobs.update

    def failing_update(job_id, **fields):
        if fields.get("status") == JobStatus.running:
            raise RuntimeError("database is locked")
        return original_update(job_id, **fields)

    jobs.update = failing_update  # type: ignore[method-assign]
    assert service.start_job("job-1") is True
    await asyncio.gather(*list(service._running.values()), return_exceptions=True)

    assert "job-1" not in service._running, "任务抛错后不能留在 _running 里变成僵尸"
    assert service.start_job("job-1") is True, "同一个 job_id 还要能被重新启动"


@pytest.mark.asyncio
async def test_same_url_concurrent_ingest_creates_one_job(tmp_path):
    """同一网址同时到达两次（Mac 重发、并发请求）只应建一个任务，不重复抓。"""
    service, jobs = build(tmp_path)

    first, second = await asyncio.gather(
        service.ingest_url(URL, auto_start=False),
        service.ingest_url(URL, auto_start=False),
    )

    assert first["status"] == "accepted"
    assert second["job_id"] == first["job_id"]
    assert jobs.counts()["total"] == 1
