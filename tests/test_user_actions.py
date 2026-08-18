"""Tests for UserActionExecutor depth clamping and dispatch."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.bus import MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore


class FakePipeline:
    def __init__(self, max_depth: int = 2):
        self.max_depth = max_depth
        self.calls: list[tuple] = []

    def max_crawl_depth(self) -> int:
        return self.max_depth

    async def admit_crawl_target(self, url: str) -> str:
        return url

    async def start_crawl(self, url: str, *, depth: int = 1, auto_download: bool = False):
        url = url.strip()
        depth = max(1, min(int(depth), 3, self.max_crawl_depth()))
        await self.execute(url, depth=depth, auto_download=auto_download)
        return {"status": "started", "url": url, "depth": depth}

    async def execute(self, url: str, depth: int = 1, auto_download: bool = False):
        self.calls.append((url, depth, auto_download))

    async def download(self, hashes: list[str]):
        pass

    async def reclassify(self, hashes: list[str]):
        pass


class CloseTrackingAwaitable:
    def __init__(self):
        self.closed = False

    def __await__(self):
        if False:
            yield None
        return None

    def close(self):
        self.closed = True


class CloseTrackingPipeline(FakePipeline):
    def __init__(self):
        super().__init__()
        self.download_awaitable = CloseTrackingAwaitable()
        self.reclassify_awaitable = CloseTrackingAwaitable()

    def download(self, hashes: list[str]):
        return self.download_awaitable

    def reclassify(self, hashes: list[str]):
        return self.reclassify_awaitable


class StartOnlyPipeline:
    def __init__(self):
        self.calls = []

    async def start_crawl(self, url: str, *, depth: int = 1, auto_download: bool = False):
        self.calls.append((url, depth, auto_download))
        return {"status": "started", "url": url.strip(), "depth": 2}


class RecordingDownloadPipeline(FakePipeline):
    """记录 download 收到的 hashes 的测试管道。"""

    def __init__(self):
        super().__init__()
        self.download_calls: list[list[str]] = []

    async def download(self, hashes: list[str]):
        self.download_calls.append(hashes)


class FakeTaskManager:
    """记录 spawn 请求并在当前事件循环调度协程的测试任务管理器。"""

    def __init__(self):
        self.created: list[str | None] = []
        self.tasks: list[asyncio.Task] = []

    def create(self, coro, name: str | None = None):
        self.created.append(name)
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self.tasks.append(task)
        return task

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*self.tasks)


def _action_dependencies(store, bus):
    async_store = AsyncItemStore(store)
    return (
        async_store,
        DiscoveryTransitions(store=async_store, bus=bus),
        ClassificationTransitions(store=async_store, bus=bus),
    )


def _make_executor(max_depth: int = 2) -> tuple[UserActionExecutor, FakePipeline]:
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = FakePipeline(max_depth=max_depth)
    async_store, discovery, classification = _action_dependencies(store, bus)
    executor = UserActionExecutor(
        store=async_store,
        pipeline=pipeline,
        task_manager=None,
        discovery=discovery,
        classification=classification,
    )
    return executor, pipeline


async def _start(executor: UserActionExecutor, depth: int):
    return await executor.start_crawl("https://example.com", depth=depth)


def test_start_crawl_uses_pipeline_start_interface():
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = StartOnlyPipeline()
    async_store, discovery, classification = _action_dependencies(store, bus)
    executor = UserActionExecutor(
        store=async_store,
        pipeline=pipeline,
        task_manager=None,
        discovery=discovery,
        classification=classification,
    )

    result = asyncio.run(executor.start_crawl(" https://example.com ", depth=5, auto_download=True))

    assert result == {"status": "started", "url": "https://example.com", "depth": 2}
    assert pipeline.calls == [(" https://example.com ", 5, True)]


def test_download_and_reclassify_close_coroutines_when_task_manager_missing():
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = CloseTrackingPipeline()
    async_store, discovery, classification = _action_dependencies(store, bus)
    # 预检只放行 pending/error 状态的条目；放入 pending 条目才能走到 spawn 分支
    asyncio.run(
        async_store.add(
            MagnetItem(
                hash="HASH",
                name="n",
                magnet="magnet:?xt=urn:btih:HASH",
                status=TaskStatus.pending,
            )
        )
    )
    executor = UserActionExecutor(
        store=async_store,
        pipeline=pipeline,
        task_manager=None,
        discovery=discovery,
        classification=classification,
    )

    download_result = asyncio.run(executor.download(["HASH"]))
    reclassify_result = asyncio.run(executor.reclassify(["HASH"]))

    assert download_result == {"status": "error", "reason": "task manager unavailable"}
    assert reclassify_result == {"status": "error", "reason": "task manager unavailable"}
    assert pipeline.download_awaitable.closed is True
    assert pipeline.reclassify_awaitable.closed is True


def test_start_crawl_clamps_depth_to_pipeline_max():
    executor, pipeline = _make_executor(max_depth=2)

    result = asyncio.run(_start(executor, depth=5))

    assert result["depth"] == 2
    assert pipeline.calls == [("https://example.com", 2, False)]


def test_start_crawl_preserves_depth_within_pipeline_max():
    executor, pipeline = _make_executor(max_depth=3)

    result = asyncio.run(_start(executor, depth=2))

    assert result["depth"] == 2
    assert pipeline.calls == [("https://example.com", 2, False)]


def test_start_crawl_applies_hard_api_cap_of_three():
    executor, pipeline = _make_executor(max_depth=5)

    result = asyncio.run(_start(executor, depth=9))

    assert result["depth"] == 3
    assert pipeline.calls == [("https://example.com", 3, False)]


def test_start_crawl_enforces_minimum_depth():
    executor, pipeline = _make_executor(max_depth=2)

    result = asyncio.run(_start(executor, depth=0))

    assert result["depth"] == 1
    assert pipeline.calls == [("https://example.com", 1, False)]


# ── download 预检：只受理 pending/error 状态的条目 ──


def _seed_items(async_store):
    async def _add():
        await async_store.add(
            MagnetItem(
                hash="PENDING1",
                name="p",
                magnet="magnet:?xt=urn:btih:PENDING1",
                status=TaskStatus.pending,
            )
        )
        await async_store.add(
            MagnetItem(
                hash="DOWNLD1",
                name="d",
                magnet="magnet:?xt=urn:btih:DOWNLD1",
                status=TaskStatus.downloading,
            )
        )

    asyncio.run(_add())


def _make_download_executor(pipeline):
    store = InMemoryItemStore()
    bus = MessageBus()
    async_store, discovery, classification = _action_dependencies(store, bus)
    _seed_items(async_store)
    task_manager = FakeTaskManager()
    executor = UserActionExecutor(
        store=async_store,
        pipeline=pipeline,
        task_manager=task_manager,
        discovery=discovery,
        classification=classification,
    )
    return executor, task_manager


def test_download_prefilter_reports_actual_accepted_count():
    pipeline = RecordingDownloadPipeline()
    executor, task_manager = _make_download_executor(pipeline)

    async def run():
        # downloading / 不存在的 hash 被跳过，只有 pending 条目被受理
        result = await executor.download(["PENDING1", "DOWNLD1", "MISSING1"])
        await task_manager.drain()
        return result

    result = asyncio.run(run())

    assert result["status"] == "started"
    assert result["count"] == 1
    assert result["skipped"] == 2
    assert task_manager.created == ["download_selected"]
    assert pipeline.download_calls == [["PENDING1"]]


def test_download_all_unsubmittable_returns_skipped_without_spawning():
    pipeline = RecordingDownloadPipeline()
    executor, task_manager = _make_download_executor(pipeline)

    result = asyncio.run(executor.download(["DOWNLD1", "MISSING1"]))

    assert result["status"] == "skipped"
    assert result["count"] == 0
    assert result["skipped"] == 2
    assert task_manager.created == []
    assert pipeline.download_calls == []


if __name__ == "__main__":
    test_start_crawl_clamps_depth_to_pipeline_max()
    test_start_crawl_preserves_depth_within_pipeline_max()
    test_start_crawl_applies_hard_api_cap_of_three()
    test_start_crawl_enforces_minimum_depth()
    print("=== user actions tests passed! ===")
