"""Tests for UserActionExecutor depth clamping and dispatch."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.bus import MessageBus
from magnet_harvester.transitions import MagnetItemTransitions
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


def _make_executor(max_depth: int = 2) -> tuple[UserActionExecutor, FakePipeline]:
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = FakePipeline(max_depth=max_depth)
    transitions = MagnetItemTransitions(store=AsyncItemStore(store), bus=bus)
    executor = UserActionExecutor(
        store=AsyncItemStore(store),
        pipeline=pipeline,
        task_manager=None,
        transitions=transitions,
    )
    return executor, pipeline


async def _start(executor: UserActionExecutor, depth: int):
    return await executor.start_crawl("https://example.com", depth=depth)


def test_start_crawl_uses_pipeline_start_interface():
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = StartOnlyPipeline()
    transitions = MagnetItemTransitions(store=AsyncItemStore(store), bus=bus)
    executor = UserActionExecutor(
        store=AsyncItemStore(store),
        pipeline=pipeline,
        task_manager=None,
        transitions=transitions,
    )

    result = asyncio.run(executor.start_crawl(" https://example.com ", depth=5, auto_download=True))

    assert result == {"status": "started", "url": "https://example.com", "depth": 2}
    assert pipeline.calls == [(" https://example.com ", 5, True)]


def test_download_and_reclassify_close_coroutines_when_task_manager_missing():
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = CloseTrackingPipeline()
    transitions = MagnetItemTransitions(store=AsyncItemStore(store), bus=bus)
    executor = UserActionExecutor(
        store=AsyncItemStore(store),
        pipeline=pipeline,
        task_manager=None,
        transitions=transitions,
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


if __name__ == "__main__":
    test_start_crawl_clamps_depth_to_pipeline_max()
    test_start_crawl_preserves_depth_within_pipeline_max()
    test_start_crawl_applies_hard_api_cap_of_three()
    test_start_crawl_enforces_minimum_depth()
    print("=== user actions tests passed! ===")
