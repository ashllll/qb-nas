"""
测试 Phase Protocols — 验证 Pipeline 编排与阶段解耦
"""

import sys
import os
import asyncio
from typing import AsyncGenerator, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus, Event, EventType, MessageBus


# ── Phase Protocols（从 pipeline.py 导入） ──

from magnet_harvester.crawler import CrawlPhase
from magnet_harvester.pipeline import (
    HarvestPipeline,
)
from magnet_harvester.utils.bg_tasks import BGTaskManager


class FakeCrawlPhase:
    """产出指定磁力链接的 Fake Phase"""

    def __init__(self, items: Optional[List[MagnetItem]] = None):
        self.items = items or []
        self.called_with: List[tuple] = []
        self.max_depth = 3

    async def admit_url(self, url: str) -> str:
        return url

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]:
        self.called_with.append((url, depth))
        yield {"type": "progress", "msg": "fake crawl"}
        for item in self.items:
            yield {"type": "found", "item": item.model_dump()}
        yield {"type": "done", "total": len(self.items), "url": url}


class FakeClassifyPhase:
    """为所有输入返回指定分类的 Fake Phase"""

    def __init__(self, category: str = "电影", confidence: float = 0.95):
        self.category = category
        self.confidence = confidence
        self.called_with: List[List[MagnetItem]] = []
        self.usage = type("Usage", (), {"as_dict": lambda self: {"total": 0}})()
        self.results: List[dict] = []

    async def classify_stream_batch(self, items: List[dict], on_result=None):
        self.called_with.append(items)
        for i, inp in enumerate(items):
            result = {
                "category": self.category,
                "save_path": f"/downloads/{self.category}",
                "confidence": str(self.confidence),
                "reason": "test",
            }
            self.results.append(result)
            if on_result:
                on_result(inp["index"], result)

    def get_cache_stats(self):
        return {"size": 0, "hits": 0, "misses": 0}


class FakeDownloadPhase:
    """追踪下载请求的 Fake Phase"""

    def __init__(self, success: bool = True):
        self.success = success
        self.called_with: List[tuple] = []
        self.last_error: str | None = None

    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool:
        self.called_with.append((magnet[:20], category, save_path))
        if not self.success:
            self.last_error = "fake download failed"
        return self.success

    def close(self):
        pass

    def is_healthy(self):
        return True

    def ping(self):
        return True


class RecordingBus(MessageBus):
    def __init__(self):
        super().__init__()
        self.events: List[Event] = []

    async def emit(self, event: Event):
        self.events.append(event)


class FakeTaskManager:
    def __init__(self):
        self.calls: List[str | None] = []

    def create(self, coro, name=None):
        self.calls.append(name)
        return asyncio.create_task(coro, name=name)


def test_crawl_phase_protocol():
    """FakeCrawlPhase 符合 CrawlPhase 协议"""
    phase = FakeCrawlPhase()
    assert isinstance(phase, CrawlPhase)


def test_pipeline_with_fake_phases():
    """用 FakePhase 测试完整编排"""
    store = FakeStore()
    bus = NullBus()

    # 添加一些已有条目
    store.add(MagnetItem(hash="ZZZZ", name="Existing", magnet="magnet:?xt=urn:btih:ZZZZ"))

    crawl_phase = FakeCrawlPhase(
        items=[
            MagnetItem(hash="AAAA", name="Movie 1", magnet="magnet:?xt=urn:btih:AAAA"),
            MagnetItem(hash="BBBB", name="Movie 2", magnet="magnet:?xt=urn:btih:BBBB"),
        ]
    )
    classify_phase = FakeClassifyPhase(category="电影")
    download_phase = FakeDownloadPhase(success=True)

    pipeline = HarvestPipeline(
        crawler=crawl_phase,
        classifier=classify_phase,
        qbit=download_phase,
        store=store,
        bus=bus,
    )

    # 执行管道（auto_download=True 触发第三阶段）
    import asyncio

    asyncio.run(pipeline.execute("https://example.com", depth=1, auto_download=True))

    # 验证 crawl phase 被调用
    assert len(crawl_phase.called_with) == 1
    assert crawl_phase.called_with[0][0] == "https://example.com"

    # 验证新的磁力链接已存储
    assert store.get("AAAA") is not None
    assert store.get("BBBB") is not None

    # 验证分类结果（下载前状态暂存为 pending）
    item_a = store.get("AAAA")
    assert item_a is not None
    assert item_a.category == "电影"

    # 验证下载被触发（auto_download=True）→ 状态变为 queued，后续由 qB 同步器推进
    assert len(download_phase.called_with) >= 1
    assert item_a.status == TaskStatus.queued
    assert item_a.torrent_state == "submitted"


def test_start_crawl_returns_trackable_task_id():
    async def run():
        store = FakeStore()
        bus = NullBus()
        task_manager = BGTaskManager()
        pipeline = HarvestPipeline(
            crawler=FakeCrawlPhase(),
            classifier=FakeClassifyPhase(),
            qbit=FakeDownloadPhase(),
            store=store,
            bus=bus,
            task_manager=task_manager,
        )

        result = await pipeline.start_crawl("https://example.com", depth=1)
        task_id = result.get("task_id")

        assert result["status"] == "started"
        assert isinstance(task_id, str)
        assert task_manager.get_task(task_id)["name"].startswith("crawl:")

        await task_manager.shutdown()

    asyncio.run(run())


def test_pipeline_skip_download():
    """auto_download=False 时不触发下载"""
    store = FakeStore()
    bus = NullBus()

    crawl_phase = FakeCrawlPhase(
        items=[
            MagnetItem(hash="CCCC", name="SkipDL", magnet="magnet:?xt=urn:btih:CCCC"),
        ]
    )
    classify_phase = FakeClassifyPhase(category="电视剧")
    download_phase = FakeDownloadPhase()

    pipeline = HarvestPipeline(
        crawler=crawl_phase,
        classifier=classify_phase,
        qbit=download_phase,
        store=store,
        bus=bus,
    )

    import asyncio

    asyncio.run(pipeline.execute("https://example.com", depth=1, auto_download=False))

    assert len(download_phase.called_with) == 0, "auto_download=False 不应触发下载"


def test_classify_item_events_are_observable_before_all_done():
    """单项分类结果应在 CLASSIFY_ALL_DONE 前完成发布"""
    store = FakeStore()
    bus = RecordingBus()

    crawl_phase = FakeCrawlPhase(
        items=[
            MagnetItem(hash="DDDD", name="Ordered", magnet="magnet:?xt=urn:btih:DDDD"),
        ]
    )
    classify_phase = FakeClassifyPhase(category="电影")
    download_phase = FakeDownloadPhase()

    pipeline = HarvestPipeline(
        crawler=crawl_phase,
        classifier=classify_phase,
        qbit=download_phase,
        store=store,
        bus=bus,
    )

    import asyncio

    asyncio.run(pipeline.execute("https://example.com", depth=1, auto_download=False))

    event_types = [event.type for event in bus.events]
    assert event_types.index(EventType.CLASSIFY_DONE) < event_types.index(
        EventType.CLASSIFY_ALL_DONE
    )
    assert event_types.index(EventType.STORE_CHANGED) < event_types.index(
        EventType.CLASSIFY_ALL_DONE
    )


def test_download_result_is_observable_after_queued_store_change():
    """下载提交成功后，queued 状态应先进入 STORE_CHANGED 再发布 DOWNLOAD_RESULT"""
    store = FakeStore()
    bus = RecordingBus()

    crawl_phase = FakeCrawlPhase(
        items=[
            MagnetItem(hash="EEEE", name="Download", magnet="magnet:?xt=urn:btih:EEEE"),
        ]
    )
    classify_phase = FakeClassifyPhase(category="电影")
    download_phase = FakeDownloadPhase(success=True)

    pipeline = HarvestPipeline(
        crawler=crawl_phase,
        classifier=classify_phase,
        qbit=download_phase,
        store=store,
        bus=bus,
    )

    import asyncio

    asyncio.run(pipeline.execute("https://example.com", depth=1, auto_download=True))

    queued_store_index = next(
        idx
        for idx, event in enumerate(bus.events)
        if event.type == EventType.STORE_CHANGED
        and event.data["item"]["hash"] == "EEEE"
        and event.data["item"]["status"] == TaskStatus.queued
        and event.data["item"]["torrent_state"] == "submitted"
    )
    download_result_index = next(
        idx
        for idx, event in enumerate(bus.events)
        if event.type == EventType.DOWNLOAD_RESULT and event.data["hash"] == "EEEE"
    )

    assert queued_store_index < download_result_index


def test_no_new_items_skips_classify():
    """没有新磁力链接时跳过分类和下载"""
    store = FakeStore()
    bus = NullBus()

    crawl_phase = FakeCrawlPhase(items=[])  # 无新条目
    classify_phase = FakeClassifyPhase()
    download_phase = FakeDownloadPhase()

    pipeline = HarvestPipeline(
        crawler=crawl_phase,
        classifier=classify_phase,
        qbit=download_phase,
        store=store,
        bus=bus,
    )

    import asyncio

    asyncio.run(pipeline.execute("https://example.com", depth=1, auto_download=True))

    assert len(classify_phase.called_with) == 0, "无新条目时不应分类"
    assert len(download_phase.called_with) == 0, "无新条目时不应下载"


def test_classify_stream_uses_injected_task_manager():
    store = FakeStore()
    bus = NullBus()
    tasks = FakeTaskManager()
    item = MagnetItem(hash="FFFF", name="UsesTaskManager", magnet="magnet:?xt=urn:btih:FFFF")

    pipeline = HarvestPipeline(
        crawler=FakeCrawlPhase(),
        classifier=FakeClassifyPhase(category="电影"),
        qbit=FakeDownloadPhase(),
        store=store,
        bus=bus,
        task_manager=tasks,
    )

    asyncio.run(pipeline._stream_classify([item]))

    assert tasks.calls == ["classify:FFFF"]


def test_reclassify_includes_error_status_items():
    """reclassify() 应包含 error 状态的条目，不应排除它们。"""
    store = FakeStore()
    bus = NullBus()

    error_item = MagnetItem(
        hash="ERR01",
        name="Error Item",
        magnet="magnet:?xt=urn:btih:ERR01",
        status=TaskStatus.error,
        category="电影",
    )
    store.add(error_item)

    classify_phase = FakeClassifyPhase(category="电视剧")

    pipeline = HarvestPipeline(
        crawler=FakeCrawlPhase(),
        classifier=classify_phase,
        qbit=FakeDownloadPhase(),
        store=store,
        bus=bus,
    )

    import asyncio
    asyncio.run(pipeline.reclassify(["ERR01"]))

    # reclassify 应将 error 状态条目发送到分类阶段
    assert len(classify_phase.called_with) == 1, "error 状态应被 reclassify 包含"
    classified_items = classify_phase.called_with[0]
    assert len(classified_items) == 1
    assert classified_items[0]["name"] == "Error Item"


if __name__ == "__main__":
    test_crawl_phase_protocol()
    test_pipeline_with_fake_phases()
    test_pipeline_skip_download()
    test_classify_item_events_are_observable_before_all_done()
    test_download_result_is_observable_after_queued_store_change()
    test_no_new_items_skips_classify()
    print("=== Phase Pipeline tests passed! ===")
