"""
P1-6: 同步循环优化测试

缺陷: _stream_classify 中 classification_started 是顺序 await，items 多时慢
修复: 使用 asyncio.gather 并发执行 classification_started
"""

import asyncio
import pytest
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.bus import MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus


class FakeQBit:
    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool:
        return True

    async def ping(self) -> bool:
        return True

    def close(self):
        pass

    def is_healthy(self) -> bool:
        return True


class FakeClassifier:
    async def classify_stream_batch(self, items, on_result=None):
        for item in items:
            if on_result:
                on_result(
                    item["index"],
                    {"category": "电影", "save_path": "/movies", "confidence": "high"},
                )

    @property
    def usage(self):
        return self

    def as_dict(self):
        return {}

    def get_cache_stats(self):
        return {}


class FakeCrawler:
    max_depth = 3

    async def crawl(self, url, depth=1):
        yield {"type": "done", "total": 0, "url": url}


def make_item(hash_val: str) -> MagnetItem:
    return MagnetItem(
        hash=hash_val,
        name=f"Test {hash_val}",
        magnet=f"magnet:?xt=urn:btih:{hash_val}",
        status=TaskStatus.pending,
    )


@pytest.mark.asyncio
async def test_classify_start_is_concurrent():
    """验证 5 个 item 的 classification_started 是并发执行的"""
    store = InMemoryItemStore()
    bus = MessageBus()

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    class TrackedTransitions:
        def __init__(self, store, bus):
            self._store = store
            self._bus = bus

        async def classification_started(self, hash_key: str):
            nonlocal active, max_active
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            async with lock:
                active -= 1

        async def classified(self, hash_key: str, result: dict):
            pass

    pipeline = HarvestPipeline(
        crawler=FakeCrawler(),
        classifier=FakeClassifier(),
        qbit=FakeQBit(),
        store=store,
        bus=bus,
    )
    # 替换 transitions
    pipeline._transitions = TrackedTransitions(store, bus)

    items = [make_item(f"hash{i}") for i in range(5)]
    await pipeline._stream_classify(items)

    assert max_active > 1, f"classification_started 应并发执行，实际最大并发 {max_active}"
