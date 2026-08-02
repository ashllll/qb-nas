"""
Test UserActionExecutor — agent tool operations (ToolExecutor collapsed in).
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.models import MagnetItem
from magnet_harvester.services.item_queries import ItemQueryExecutor
from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.transitions import MagnetItemTransitions


class FakePipeline:
    def __init__(self):
        self.crawl_urls = []
        self.download_hashes = []
        self.reclassify_hashes = []
        self.ingested_items = []

    def max_crawl_depth(self):
        return 2

    async def admit_crawl_target(self, url):
        return url

    async def start_crawl(self, url, *, depth=1, auto_download=False):
        try:
            url = await self.admit_crawl_target(url.strip())
        except ValueError as exc:
            return {"status": "error", "reason": str(exc)}
        depth = max(1, min(int(depth), 3, self.max_crawl_depth()))
        await self.execute(url, depth=depth, auto_download=auto_download)
        return {"status": "started", "url": url, "depth": depth}

    async def execute(self, url, depth=1, auto_download=False):
        self.crawl_urls.append((url, depth, auto_download))

    async def download(self, hashes):
        self.download_hashes.extend(hashes)

    async def reclassify(self, hashes):
        self.reclassify_hashes.extend(hashes)

    async def ingest(self, items, *, auto_download=False):
        self.ingested_items.extend(items)
        return [item.hash for item in items]


class FakeTaskManager:
    def __init__(self):
        self.calls = []

    def create(self, coro, name=None):
        self.calls.append(name)
        return asyncio.create_task(coro, name=name)


def _make_executor(store=None, pipeline=None, tasks=None, stats=None):
    store = store or FakeStore()
    pipeline = pipeline or FakePipeline()
    tasks = tasks or FakeTaskManager()
    transitions = MagnetItemTransitions(store=store, bus=NullBus())
    return UserActionExecutor(
        store=store,
        pipeline=pipeline,
        task_manager=tasks,
        transitions=transitions,
        stats=stats,
    )


@pytest.mark.asyncio
async def test_get_stats():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="a", magnet="m:?xt=urn:btih:A", category="电影"))
    store.add(MagnetItem(hash="B", name="b", magnet="m:?xt=urn:btih:B", category="电视剧"))

    queries = ItemQueryExecutor(store=store)
    result = await queries.get_stats()

    assert result["total"] == 2
    assert result["by_category"]["电影"] == 1
    assert result["by_category"]["电视剧"] == 1


@pytest.mark.asyncio
async def test_list_items():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="Alpha", magnet="m:?xt=urn:btih:A", category="电影"))

    queries = ItemQueryExecutor(store=store)
    result = await queries.list_items(category="电影")

    assert result["count"] == 1
    assert result["items"][0]["name"] == "Alpha"


@pytest.mark.asyncio
async def test_page_items_returns_api_payload_shape():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="Alpha", magnet="m:?xt=urn:btih:A", category="电影"))
    store.add(MagnetItem(hash="B", name="Beta", magnet="m:?xt=urn:btih:B", category="电视剧"))

    queries = ItemQueryExecutor(store=store)
    result = await queries.page_items(limit=1, offset=1)

    assert result["total"] == 2
    assert result["limit"] == 1
    assert result["offset"] == 1
    assert len(result["items"]) == 1
    assert "status" in result["items"][0]


@pytest.mark.asyncio
async def test_page_items_normalizes_internal_pagination_boundaries():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="Alpha", magnet="m:?xt=urn:btih:A", category="电影"))

    queries = ItemQueryExecutor(store=store)
    result = await queries.page_items(limit=-1, offset=-1)

    assert result["total"] == 1
    assert result["limit"] == 0
    assert result["offset"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_search_items():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="Alpha Movie", magnet="m:?xt=urn:btih:A"))
    store.add(MagnetItem(hash="B", name="Beta Show", magnet="m:?xt=urn:btih:B"))

    queries = ItemQueryExecutor(store=store)
    result = await queries.search_items(query="alpha")

    assert result["count"] == 1
    assert result["results"][0]["name"] == "Alpha Movie"


@pytest.mark.asyncio
async def test_start_crawl():
    pipeline = FakePipeline()
    tasks = FakeTaskManager()
    actions = _make_executor(pipeline=pipeline, tasks=tasks)
    result = await actions.start_crawl("https://example.com", depth=2)
    await asyncio.sleep(0)

    assert result["status"] == "started"
    assert pipeline.crawl_urls[0][0] == "https://example.com"
    assert tasks.calls == []


@pytest.mark.asyncio
async def test_start_crawl_rejects_target_denied_by_pipeline():
    pipeline = FakePipeline()

    async def reject(_url):
        raise ValueError("URL resolves to a private address")

    pipeline.admit_crawl_target = reject
    actions = _make_executor(pipeline=pipeline)

    result = await actions.start_crawl("https://internal.example")

    assert result == {"status": "error", "reason": "URL resolves to a private address"}


@pytest.mark.asyncio
async def test_add_to_queue():
    pipeline = FakePipeline()
    tasks = FakeTaskManager()
    actions = _make_executor(pipeline=pipeline, tasks=tasks)
    result = await actions.download(["A", "B"], task_name="download_batch")
    await asyncio.sleep(0)

    assert result["status"] == "started"
    assert result["count"] == 2
    assert pipeline.download_hashes == ["A", "B"]
    assert tasks.calls == ["download_batch"]


@pytest.mark.asyncio
async def test_ingest_schedules_download_through_managed_action_path():
    pipeline = FakePipeline()
    tasks = FakeTaskManager()
    actions = _make_executor(pipeline=pipeline, tasks=tasks)
    item = MagnetItem(hash="CLIP", name="Clipboard", magnet="magnet:?xt=urn:btih:CLIP")

    accepted = await actions.ingest([item], auto_download=True)
    await asyncio.sleep(0)

    assert accepted == ["CLIP"]
    assert pipeline.ingested_items == [item]
    assert pipeline.download_hashes == ["CLIP"]
    assert tasks.calls == ["clipboard_download"]


def test_manually_reclassify():
    store = FakeStore()
    store.add(
        MagnetItem(
            hash="ABCDEF123456", name="Test", magnet="m:?xt=urn:btih:ABCDEF123456", category="电影"
        )
    )

    actions = _make_executor(store=store)
    result = asyncio.run(actions.manually_reclassify("ABCDEF12", "电视剧"))

    assert result["status"] == "ok"
    assert store.get("ABCDEF123456").category == "电视剧"


def test_clear_all():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="a", magnet="m:?xt=urn:btih:A"))

    actions = _make_executor(store=store)
    result = asyncio.run(actions.clear_items())

    assert result["status"] == "cleared"
    assert store.count == 0


if __name__ == "__main__":
    test_get_stats()
    test_list_items()
    test_search_items()
    test_start_crawl()
    test_add_to_queue()
    test_manually_reclassify()
    test_clear_all()
    print("=== user_action_executor tests passed! ===")
