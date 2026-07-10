"""
P2-25: Agent depth 限制测试 (UserActionExecutor)
"""

import asyncio
import pytest
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.bus import MessageBus
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions


class FakePipeline:
    def __init__(self, max_depth=2):
        self.max_depth = max_depth
        self.last_depth = None

    def max_crawl_depth(self):
        return self.max_depth

    async def admit_crawl_target(self, url):
        return url

    async def start_crawl(self, url, *, depth=1, auto_download=False):
        url = await self.admit_crawl_target(url.strip())
        depth = max(1, min(int(depth), 3, self.max_crawl_depth()))
        await self.execute(url, depth=depth, auto_download=auto_download)
        return {"status": "started", "url": url, "depth": depth}

    async def execute(self, url, depth=1, auto_download=False):
        self.last_depth = depth

    async def download(self, hashes):
        pass

    async def reclassify(self, hashes):
        pass


def _make_executor(store, pipeline):
    bus = MessageBus()
    return UserActionExecutor(
        store=store,
        pipeline=pipeline,
        task_manager=None,
        discovery=DiscoveryTransitions(store=store, bus=bus),
        classification=ClassificationTransitions(store=store, bus=bus),
    )


@pytest.mark.asyncio
async def test_start_crawl_depth_clamped():
    """验证 depth 被限制在 1-min(3, max_crawl_depth) 范围内"""
    store = InMemoryItemStore()
    pipeline = FakePipeline()
    executor = _make_executor(store, pipeline)

    result = await executor.start_crawl("http://example.com", depth=0)
    assert result["depth"] == 1
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 1

    result = await executor.start_crawl("http://example.com", depth=5)
    assert result["depth"] == 2
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 2

    result = await executor.start_crawl("http://example.com", depth=10)
    assert result["depth"] == 2
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 2

    result = await executor.start_crawl("http://example.com", depth=2)
    assert result["depth"] == 2
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 2


@pytest.mark.asyncio
async def test_start_crawl_respects_pipeline_max_depth():
    """当 pipeline 允许更深时，硬上限 3 仍然生效"""
    store = InMemoryItemStore()
    pipeline = FakePipeline(max_depth=5)
    executor = _make_executor(store, pipeline)

    result = await executor.start_crawl("http://example.com", depth=10)
    assert result["depth"] == 3
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 3
