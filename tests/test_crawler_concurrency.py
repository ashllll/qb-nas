"""Tests for crawler concurrency configuration."""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler


class TestCrawlerConcurrency:
    """Verify Scrapling receives bounded concurrency settings."""

    @pytest.fixture
    def crawler(self):
        return MagnetCrawler(config=CrawlerConfig())

    def test_start_passes_bounded_concurrency_to_scrapling(self, crawler, monkeypatch):
        captured = {}

        class FakeSession:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

        monkeypatch.setattr("magnet_harvester.crawler.AsyncDynamicSession", FakeSession)

        asyncio.run(crawler.start())
        asyncio.run(crawler.stop())

        assert captured["max_pages"] == 6

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
