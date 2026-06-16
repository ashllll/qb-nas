"""Tests for UserActionExecutor depth clamping and dispatch."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.bus import MessageBus
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.store import InMemoryItemStore


class FakePipeline:
    def __init__(self, max_depth: int = 2):
        self.max_depth = max_depth
        self.calls: list[tuple] = []

    def max_crawl_depth(self) -> int:
        return self.max_depth

    async def admit_crawl_target(self, url: str) -> str:
        return url

    async def execute(self, url: str, depth: int = 1, auto_download: bool = False):
        self.calls.append((url, depth, auto_download))

    async def download(self, hashes: list[str]):
        pass

    async def reclassify(self, hashes: list[str]):
        pass


def _make_executor(max_depth: int = 2) -> tuple[UserActionExecutor, FakePipeline]:
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = FakePipeline(max_depth=max_depth)
    transitions = MagnetItemTransitions(store=store, bus=bus)
    executor = UserActionExecutor(
        store=store,
        pipeline=pipeline,
        task_manager=None,
        transitions=transitions,
    )
    return executor, pipeline


async def _start(executor: UserActionExecutor, depth: int):
    return await executor.start_crawl("https://example.com", depth=depth)


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
