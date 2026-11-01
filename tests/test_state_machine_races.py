"""状态机竞态：分类、下载提交、qB 同步是并发的，谁也不能覆盖别人刚写好的状态。

历史实现是「先 get 再 update」：分类线程读到 pending 之后，用户点了下载把状态写成
adding，分类再无条件写回 classifying → pending，条目从下载里「消失」。
这里用 update_if_status 的条件写入守住每个转换的前置状态。
"""

from __future__ import annotations

import pytest

from magnet_harvester.bus import MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import ClassificationTransitions

HASH = "A" * 40


def build_item(status: TaskStatus, name: str = "item") -> MagnetItem:
    return MagnetItem(
        hash=HASH,
        name=name,
        magnet=f"magnet:?xt=urn:btih:{HASH}",
        status=status,
    )


async def _setup(status: TaskStatus):
    store = AsyncItemStore(InMemoryItemStore())
    store._backend.add(build_item(status))
    bus = MessageBus()
    return store, ClassificationTransitions(store=store, bus=bus)


# ── 分类开始 ─────────────────────────────────


@pytest.mark.asyncio
async def test_started_only_moves_pending_to_classifying():
    store, classification = await _setup(TaskStatus.pending)
    await classification.started(HASH)
    item = await store.get(HASH)
    assert item is not None and item.status == TaskStatus.classifying


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [TaskStatus.adding, TaskStatus.queued, TaskStatus.downloading, TaskStatus.paused, TaskStatus.success],
)
async def test_started_does_not_clobber_active_states(status):
    """用户已经点过下载（adding）或 qB 里已经在跑（downloading/paused）时，分类不能改状态。"""
    store, classification = await _setup(status)
    await classification.started(HASH)
    item = await store.get(HASH)
    assert item is not None
    assert item.status == status


@pytest.mark.asyncio
async def test_started_allows_error_reclassify():
    store, classification = await _setup(TaskStatus.error)
    await classification.started(HASH)
    item = await store.get(HASH)
    assert item is not None and item.status == TaskStatus.classifying


# ── 分类结束 ─────────────────────────────────


@pytest.mark.asyncio
async def test_classified_only_applies_while_classifying():
    store, classification = await _setup(TaskStatus.classifying)
    await classification.classified(HASH, {"category": "电影", "save_path": "/x"})
    item = await store.get(HASH)
    assert item is not None
    assert item.status == TaskStatus.pending
    assert item.category == "电影"


@pytest.mark.asyncio
async def test_classified_does_not_overwrite_download_in_progress():
    """分类结果晚到，但用户已经点了下载：不能再把状态写回 pending。"""
    store, classification = await _setup(TaskStatus.adding)
    await classification.classified(HASH, {"category": "电影", "save_path": "/x"})
    item = await store.get(HASH)
    assert item is not None
    assert item.status == TaskStatus.adding


@pytest.mark.asyncio
async def test_classification_failed_does_not_overwrite_active_download():
    store, classification = await _setup(TaskStatus.downloading)
    await classification.failed(HASH, "分类失败")
    item = await store.get(HASH)
    assert item is not None
    assert item.status == TaskStatus.downloading


# ── 重新分类的候选过滤 ─────────────────────────


class FakeClassifier:
    def __init__(self):
        self.calls = 0

    async def classify_stream_batch(self, items, on_result=None):
        self.calls += 1
        for entry in items:
            if on_result is not None:
                on_result(entry["index"], {"category": "电影", "save_path": "/x"})


class FakeClassification:
    def __init__(self):
        self.started: list[str] = []

    async def started(self, hash_key):
        self.started.append(hash_key)

    async def classified(self, hash_key, result):
        return None

    async def failed(self, hash_key, error_msg):
        return None


class FakeQbit:
    last_error = None

    async def add_magnet(self, *args, **kwargs):
        return True

    async def ping(self):
        return True

    def close(self):
        return None

    def is_healthy(self):
        return True


@pytest.mark.asyncio
async def test_reclassify_skips_paused_items():
    store = AsyncItemStore(InMemoryItemStore())
    store._backend.add(build_item(TaskStatus.paused))
    classifier = FakeClassifier()
    classification = FakeClassification()
    pipeline = HarvestPipeline(
        crawler=None,
        classifier=classifier,
        qbit=FakeQbit(),
        store=store,
        bus=MessageBus(),
        classification=classification,
    )

    await pipeline.reclassify([HASH])

    assert classifier.calls == 0
    assert classification.started == []
    item = await store.get(HASH)
    assert item is not None and item.status == TaskStatus.paused
