"""Tests for crawler concurrency configuration."""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler


class TestCrawlerConcurrency:
    """Verify crawl4ai receives bounded concurrency settings."""

    @pytest.fixture
    def crawler(self):
        return MagnetCrawler(config=CrawlerConfig())

    def test_run_config_passes_bounded_concurrency_to_crawl4ai(self, crawler):
        cfg = crawler._build_run_config(stream=True)

        assert cfg.semaphore_count == 6

    def test_worker_count_is_capped_for_browser_sessions(self):
        crawler = MagnetCrawler(config=CrawlerConfig(concurrency=50))

        assert crawler._worker_count == 8

    def test_seen_set_no_duplicates_under_race(self, crawler):
        """Two workers adding the same hash should not duplicate."""
        seen = set()

        async def worker(hash_key):
            async with crawler._seen_lock:
                if hash_key in seen:
                    return False
                seen.add(hash_key)
                return True

        async def main():
            w1 = asyncio.create_task(worker("abc123"))
            w2 = asyncio.create_task(worker("abc123"))
            return await asyncio.gather(w1, w2)

        r1, r2 = asyncio.run(main())
        assert not (r1 and r2), "Both workers claimed the same hash"
