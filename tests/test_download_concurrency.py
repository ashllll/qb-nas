"""
P1-7: 下载并发化测试

缺陷: _download_items 是顺序循环，每次下载后 sleep(0.3)，大量 item 时效率低
修复: 使用 asyncio.Semaphore 限制并发，asyncio.gather 并行下载
"""
import asyncio
import pytest
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.bus import MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus


class FakeQBit:
    def __init__(self):
        self.last_error = None
        self._calls = []
        self._delay = 0.1

    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool:
        await asyncio.sleep(self._delay)
        self._calls.append((magnet, category, save_path))
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
                on_result(item["index"], {"category": "电影", "save_path": "/movies", "confidence": "high"})

    @property
    def usage(self):
        return self

    def as_dict(self):
        return {}

    def get_cache_stats(self):
        return {}


class FakeCrawler:
    async def crawl(self, url, depth=1):
        yield {"type": "done", "total": 0, "url": url}


def make_item(hash_val: str) -> MagnetItem:
    return MagnetItem(
        hash=hash_val,
        name=f"Test {hash_val}",
        magnet=f"magnet:?xt=urn:btih:{hash_val}",
        status=TaskStatus.pending,
        category="电影",
        save_path="/movies",
    )


@pytest.mark.asyncio
async def test_download_is_concurrent():
    """验证 5 个 item 并发下载的总时间 < 顺序下载时间"""
    store = InMemoryItemStore()
    bus = MessageBus()
    qbit = FakeQBit()
    qbit._delay = 0.1  # 每个下载 0.1s

    pipeline = HarvestPipeline(
        crawler=FakeCrawler(),
        classifier=FakeClassifier(),
        qbit=qbit,
        store=store,
        bus=bus,
    )

    for i in range(5):
        store.add(make_item(f"hash{i}"))

    start = asyncio.get_event_loop().time()
    await pipeline.download([f"hash{i}" for i in range(5)])
    elapsed = asyncio.get_event_loop().time() - start

    # 顺序下载需要 5 * 0.1 = 0.5s + sleep
    # 并发下载（默认 concurrency=3）需要 ceil(5/3) * 0.1 = 0.2s
    assert elapsed < 0.4, f"并发下载应更快，实际耗时 {elapsed}s"
    assert len(qbit._calls) == 5


@pytest.mark.asyncio
async def test_download_concurrency_limit():
    """验证 Semaphore 限制并发数"""
    store = InMemoryItemStore()
    bus = MessageBus()
    qbit = FakeQBit()

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def tracked_add_magnet(magnet, category, save_path):
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1
        return True

    qbit.add_magnet = tracked_add_magnet

    pipeline = HarvestPipeline(
        crawler=FakeCrawler(),
        classifier=FakeClassifier(),
        qbit=qbit,
        store=store,
        bus=bus,
    )

    for i in range(10):
        store.add(make_item(f"hash{i}"))

    await pipeline._download_items([f"hash{i}" for i in range(10)], concurrency=3)
    assert max_active <= 3, f"并发数应被限制为 3，实际最大 {max_active}"
