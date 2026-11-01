"""采集中心入口：磁力入库、网址任务、逐条结果与原因、重启续跑。"""

from __future__ import annotations

import asyncio
import base64

import pytest

from magnet_harvester.bus import EventType, MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.jobs import JobStatus, JobStore
from magnet_harvester.models import IngestKind, IngestRequest, TaskStatus
from magnet_harvester.services.ingest import IngestService
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions
from magnet_harvester.utils.bg_tasks import BGTaskManager

HEX = "0123456789ABCDEF0123456789ABCDEF01234567"
HEX_B32 = base64.b32encode(bytes.fromhex(HEX)).decode()
MAGNET = f"magnet:?xt=urn:btih:{HEX}&dn=Movie.2024.1080p"
ROOT_URL = "https://example.com/torrents/search/4/sexart"


class RecordingCrawler:
    """按剧本产出取页事件的替身（真正的取页解析在 scrapling_spider 里，不属于本块）。"""

    def __init__(self, events: list[dict]):
        self.events = events
        self.calls: list[tuple[str, int]] = []
        self.max_depth = 2

    async def crawl(self, url: str, depth: int = 1):
        self.calls.append((url, depth))
        for event in self.events:
            yield event


class RecordingBus(MessageBus):
    def __init__(self):
        super().__init__()
        self.events: list = []

    async def emit(self, event):
        self.events.append(event)
        await super().emit(event)


def found_event(hash_value: str, name: str, detail_url: str | None = None) -> dict:
    payload = {
        "hash": hash_value,
        "name": name,
        "magnet": f"magnet:?xt=urn:btih:{hash_value}&dn={name}",
        "size": "1234",
        "source_url": detail_url or ROOT_URL,
        "detail_url": detail_url,
    }
    return {"type": "found", "item": payload}


def build(tmp_path, crawler=None, *, task_manager=None):
    store = AsyncItemStore(InMemoryItemStore())
    bus = RecordingBus()
    jobs = JobStore(tmp_path / "jobs.db")
    service = IngestService(
        store=store,
        jobs=jobs,
        classifier=LocalClassifier(),
        crawler=crawler or RecordingCrawler([]),
        bus=bus,
        discovery=DiscoveryTransitions(store=store, bus=bus),
        classification=ClassificationTransitions(store=store, bus=bus),
        task_manager=task_manager,
    )
    return service, store, jobs, bus


# ── 磁力 ────────────────────────────────────


@pytest.mark.asyncio
async def test_magnet_ingest_stores_classified_item_without_download(tmp_path):
    service, store, _jobs, _bus = build(tmp_path)

    result = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value=MAGNET)
    )

    assert result["ok"] is True
    assert result["status"] == "accepted"
    item = await store.get(HEX)
    assert item is not None
    assert item.hash == HEX
    assert item.category  # 走了现有分类规则链
    # 默认 A：只入库，状态停在 pending（等用户点选才提交 qB）
    assert item.status == TaskStatus.pending
    assert item.source_kind == "mac_magnet"


@pytest.mark.asyncio
async def test_magnet_ingest_dedupes_by_normalized_infohash(tmp_path):
    service, store, _jobs, _bus = build(tmp_path)

    first = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value=MAGNET)
    )
    # 同一磁力、不同写法（base32）、不同 event_id → 库里已有
    second = await service.handle_event(
        IngestRequest(
            event_id="evt-2",
            kind=IngestKind.magnet,
            value=f"magnet:?xt=urn:btih:{HEX_B32}&dn=Movie.2024.1080p",
        )
    )

    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert second["item_id"] == HEX
    assert await store.count() == 1


@pytest.mark.asyncio
async def test_same_event_id_is_idempotent(tmp_path):
    service, store, _jobs, _bus = build(tmp_path)

    first = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value=MAGNET)
    )
    replay = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value=MAGNET)
    )

    assert first["status"] == "accepted"
    assert replay["status"] == "duplicate"
    assert replay["item_id"] == HEX
    assert "幂等" in replay["reason"]
    assert await store.count() == 1


@pytest.mark.asyncio
async def test_invalid_magnet_is_rejected_with_reason(tmp_path):
    service, store, _jobs, _bus = build(tmp_path)

    result = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value="magnet:?dn=nothing")
    )

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert "btih" in result["reason"]
    assert await store.count() == 0


# ── 网址 ────────────────────────────────────


@pytest.mark.asyncio
async def test_unsafe_url_is_rejected_before_any_fetch(tmp_path):
    crawler = RecordingCrawler([])
    service, _store, jobs, _bus = build(tmp_path, crawler)

    result = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.url, value="http://192.168.1.1:8080/x")
    )

    assert result["status"] == "rejected"
    assert "不安全" in result["reason"]
    assert crawler.calls == []
    assert jobs.counts()["total"] == 0


@pytest.mark.asyncio
async def test_url_job_streams_items_and_reports_batch_summary(tmp_path):
    crawler = RecordingCrawler(
        [
            found_event(HEX, "First.1080p", "https://example.com/torrents/details/1"),
            {"type": "progress", "msg": "发现 1 个新磁力", "metrics": {"pages_crawled": 1}},
            {
                "type": "done",
                "total": 1,
                "url": ROOT_URL,
                "metrics": {
                    "elapsed_sec": 1.4,
                    "pages_crawled": 2,
                    "magnets_found": 1,
                    "first_item_ms": 640,
                    "browser_used": False,
                },
            },
        ]
    )
    service, store, jobs, bus = build(tmp_path, crawler)

    started = await service.ingest_url(ROOT_URL, source="mac", batch_id="batch-9", auto_start=False)
    assert started["status"] == "accepted"
    job_id = started["job_id"]

    await service._run_job(job_id)  # 直接跑一遍，避免依赖后台调度时序

    job = jobs.get(job_id)
    assert job is not None
    assert job.status == JobStatus.done
    assert job.found == 1 and job.added == 1
    assert job.summary == "从 1 个网址收到 1 条"
    assert job.first_item_ms is not None
    assert job.browser_used is False

    item = await store.get(HEX)
    assert item is not None
    # 根 URL 与详情页都留痕：看得出「来自哪个网址」，也留得下更细的出处
    assert item.source_url == ROOT_URL
    assert item.detail_url == "https://example.com/torrents/details/1"
    assert item.batch_id == "batch-9"
    assert item.source_kind == "mac_url"
    assert item.category

    job_events = [e for e in bus.events if e.type == EventType.JOB_UPDATED]
    assert job_events, "任务状态变化要能被页面实时看到"
    assert any(e.data.get("status") == "done" for e in job_events)


@pytest.mark.asyncio
async def test_url_job_without_magnets_explains_why(tmp_path):
    crawler = RecordingCrawler(
        [
            {
                "type": "error",
                "msg": "详情页被 Cloudflare 挡住",
                "url": f"{ROOT_URL}/torrents/details/7",
                "reason": "blocked",
            },
            {"type": "done", "total": 0, "url": ROOT_URL, "metrics": {"pages_crawled": 1}},
        ]
    )
    service, store, jobs, _bus = build(tmp_path, crawler)

    started = await service.ingest_url(ROOT_URL, source="mac", auto_start=False)
    await service._run_job(started["job_id"])

    job = jobs.get(started["job_id"])
    assert job is not None
    assert job.status == JobStatus.done
    assert job.found == 0
    assert "blocked" in job.reason
    assert job.errors and job.errors[0]["reason"] == "blocked"
    assert job.summary == "没有找到磁力：被站点挡住"
    assert await store.count() == 0


@pytest.mark.asyncio
async def test_same_url_while_running_reuses_job(tmp_path):
    service, _store, jobs, _bus = build(tmp_path, RecordingCrawler([]))

    first = await service.ingest_url(ROOT_URL, source="mac", auto_start=False)
    second = await service.ingest_url(ROOT_URL, source="mac", auto_start=False)

    assert second["job_id"] == first["job_id"]
    assert "复用" in second["reason"]
    assert jobs.counts()["total"] == 1


# ── 重启续跑 ─────────────────────────────────


@pytest.mark.asyncio
async def test_resume_pending_reruns_interrupted_job(tmp_path):
    crawler = RecordingCrawler(
        [found_event(HEX, "After.Restart.1080p"), {"type": "done", "total": 1, "url": ROOT_URL}]
    )
    manager = BGTaskManager()
    service, store, jobs, _bus = build(tmp_path, crawler, task_manager=manager)

    created = await service.ingest_url(ROOT_URL, source="mac", auto_start=False)
    # 模拟「抓取中进程被杀」：库里留下 running
    jobs.update(created["job_id"], status=JobStatus.running, started_at="2026-11-01T10:00:00")

    # 新进程：同一个 DB 重新构建服务，启动时续跑
    restarted = IngestService(
        store=store,
        jobs=JobStore(tmp_path / "jobs.db"),
        classifier=LocalClassifier(),
        crawler=crawler,
        bus=RecordingBus(),
        discovery=DiscoveryTransitions(store=store, bus=MessageBus()),
        classification=ClassificationTransitions(store=store, bus=MessageBus()),
        task_manager=manager,
    )
    resumed = await restarted.resume_pending()

    assert resumed == 1
    await asyncio.gather(*restarted._running.values(), return_exceptions=True)
    await manager.shutdown()

    job = restarted.get_job(created["job_id"])
    assert job is not None
    assert job["status"] == JobStatus.done
    assert job["added"] == 1
    assert job["attempts"] == 1
    assert await store.get(HEX) is not None


@pytest.mark.asyncio
async def test_shutdown_returns_running_job_to_queue(tmp_path):
    class SlowCrawler(RecordingCrawler):
        async def crawl(self, url: str, depth: int = 1):
            self.calls.append((url, depth))
            yield found_event(HEX, "Slow.1080p")
            await asyncio.sleep(30)
            yield {"type": "done", "total": 1, "url": url}

    manager = BGTaskManager()
    service, _store, jobs, _bus = build(tmp_path, SlowCrawler([]), task_manager=manager)
    started = await service.ingest_url(ROOT_URL, source="mac")  # 自动排上后台任务
    await asyncio.sleep(0.05)

    await service.shutdown()

    job = jobs.get(started["job_id"])
    assert job is not None
    assert job.status == JobStatus.queued
    assert job.message == "服务关闭，重启后继续"
    await manager.shutdown()
