"""
Test ToolExecutor — agent tool dispatch service.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.models import MagnetItem
from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus
from magnet_harvester.services.agent_tools import ToolExecutor


class FakePipeline:
    def __init__(self):
        self.crawl_urls = []
        self.download_hashes = []
        self.reclassify_hashes = []

    def max_crawl_depth(self):
        return 2

    async def admit_crawl_target(self, url):
        return url

    async def execute(self, url, depth=1, auto_download=False):
        self.crawl_urls.append((url, depth, auto_download))

    async def download(self, hashes):
        self.download_hashes.extend(hashes)

    async def reclassify(self, hashes):
        self.reclassify_hashes.extend(hashes)


class FakeTaskManager:
    def __init__(self):
        self.calls = []

    def create(self, coro, name=None):
        self.calls.append(name)
        return asyncio.create_task(coro, name=name)


def test_get_stats():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="a", magnet="m:?xt=urn:btih:A", category="电影"))
    store.add(MagnetItem(hash="B", name="b", magnet="m:?xt=urn:btih:B", category="电视剧"))

    executor = ToolExecutor(store=store, pipeline=None, bus=NullBus())
    result = asyncio.run(executor.execute("get_stats", {}))

    assert result["total"] == 2
    assert result["by_category"]["电影"] == 1
    assert result["by_category"]["电视剧"] == 1


def test_list_items():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="Alpha", magnet="m:?xt=urn:btih:A", category="电影"))

    executor = ToolExecutor(store=store, pipeline=None, bus=NullBus())
    result = asyncio.run(executor.execute("list_items", {"category": "电影"}))

    assert result["count"] == 1
    assert result["items"][0]["name"] == "Alpha"


def test_search_items():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="Alpha Movie", magnet="m:?xt=urn:btih:A"))
    store.add(MagnetItem(hash="B", name="Beta Show", magnet="m:?xt=urn:btih:B"))

    executor = ToolExecutor(store=store, pipeline=None, bus=NullBus())
    result = asyncio.run(executor.execute("search_items", {"query": "alpha"}))

    assert result["count"] == 1
    assert result["results"][0]["name"] == "Alpha Movie"


@pytest.mark.asyncio
async def test_start_crawl():
    pipeline = FakePipeline()
    tasks = FakeTaskManager()
    executor = ToolExecutor(store=FakeStore(), pipeline=pipeline, bus=NullBus(), task_manager=tasks)
    result = await executor.execute("start_crawl", {"url": "https://example.com", "depth": 2})
    await asyncio.sleep(0)

    assert result["status"] == "started"
    assert pipeline.crawl_urls[0][0] == "https://example.com"
    assert tasks.calls == ["crawl:https://example.com"]


@pytest.mark.asyncio
async def test_start_crawl_rejects_target_denied_by_pipeline():
    pipeline = FakePipeline()

    async def reject(_url):
        raise ValueError("URL resolves to a private address")

    pipeline.admit_crawl_target = reject
    executor = ToolExecutor(store=FakeStore(), pipeline=pipeline, bus=NullBus())

    result = await executor.execute("start_crawl", {"url": "https://internal.example"})

    assert result == {"status": "error", "reason": "URL resolves to a private address"}


@pytest.mark.asyncio
async def test_add_to_queue():
    pipeline = FakePipeline()
    tasks = FakeTaskManager()
    executor = ToolExecutor(store=FakeStore(), pipeline=pipeline, bus=NullBus(), task_manager=tasks)
    result = await executor.execute("add_to_queue", {"hashes": ["A", "B"]})
    await asyncio.sleep(0)

    assert result["status"] == "started"
    assert result["count"] == 2
    assert pipeline.download_hashes == ["A", "B"]
    assert tasks.calls == ["download_batch"]


def test_reclassify_item():
    store = FakeStore()
    store.add(MagnetItem(hash="ABCDEF123456", name="Test", magnet="m:?xt=urn:btih:ABCDEF123456", category="电影"))

    executor = ToolExecutor(store=store, pipeline=None, bus=NullBus())
    result = asyncio.run(executor.execute("reclassify_item", {"hash": "ABCDEF12", "category": "电视剧"}))

    assert result["status"] == "ok"
    assert store.get("ABCDEF123456").category == "电视剧"


def test_clear_all():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="a", magnet="m:?xt=urn:btih:A"))

    executor = ToolExecutor(store=store, pipeline=None, bus=NullBus())
    result = asyncio.run(executor.execute("clear_all", {"confirm": True}))

    assert result["status"] == "cleared"
    assert store.count == 0


def test_unknown_tool():
    executor = ToolExecutor(store=FakeStore(), pipeline=None, bus=NullBus())
    result = asyncio.run(executor.execute("unknown_tool", {}))

    assert "error" in result
    assert "unknown_tool" in result["error"]


if __name__ == "__main__":
    import asyncio
    test_get_stats()
    test_list_items()
    test_search_items()
    test_start_crawl()
    test_add_to_queue()
    test_reclassify_item()
    test_clear_all()
    test_unknown_tool()
    print("=== tool executor tests passed! ===")
