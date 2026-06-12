"""
P2-25: Agent depth 限制测试

缺陷: start_crawl 不经过 CrawlRequest 的 Pydantic validator，Agent 可能传入 depth=10
修复: 在 ToolExecutor.start_crawl 中添加 depth = max(1, min(depth, 3))
"""
import asyncio
import pytest
from magnet_harvester.services.agent_tools import ToolExecutor
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.bus import MessageBus


class FakePipeline:
    def __init__(self):
        self.last_depth = None

    async def execute(self, url, depth=1, auto_download=False):
        self.last_depth = depth

    async def download(self, hashes):
        pass

    async def reclassify(self, hashes):
        pass


@pytest.mark.asyncio
async def test_start_crawl_depth_clamped():
    """验证 depth 被限制在 1-3 范围内"""
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = FakePipeline()
    executor = ToolExecutor(store, pipeline, bus)

    # depth = 0 → 限制为 1
    result = await executor.execute("start_crawl", {"url": "http://example.com", "depth": 0})
    assert result["depth"] == 1
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 1

    # depth = 5 → 限制为 3
    result = await executor.execute("start_crawl", {"url": "http://example.com", "depth": 5})
    assert result["depth"] == 3
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 3

    # depth = 10 → 限制为 3
    result = await executor.execute("start_crawl", {"url": "http://example.com", "depth": 10})
    assert result["depth"] == 3
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 3

    # depth = 2 → 保持不变
    result = await executor.execute("start_crawl", {"url": "http://example.com", "depth": 2})
    assert result["depth"] == 2
    await asyncio.sleep(0.1)
    assert pipeline.last_depth == 2
