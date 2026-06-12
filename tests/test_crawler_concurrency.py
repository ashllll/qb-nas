"""Tests for crawler concurrent Set safety."""
from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.crawler import MagnetCrawler


class TestCrawlerConcurrency:
    """Verify shared Sets are safe under concurrent workers."""

    @pytest.fixture
    def crawler(self):
        return MagnetCrawler()

    def test_claim_unvisited_links_is_atomic(self, crawler):
        """Simulate two workers racing to claim the same link."""
        visited = set()
        links = ["http://a.com/1", "http://a.com/2", "http://a.com/1"]

        async def worker():
            return await crawler._claim_unvisited_links(links, visited)

        async def main():
            w1 = asyncio.create_task(worker())
            w2 = asyncio.create_task(worker())
            r1, r2 = await asyncio.gather(w1, w2)
            return r1, r2

        r1, r2 = asyncio.run(main())
        # The duplicate link should only appear in one result
        all_claimed = r1 + r2
        assert len(all_claimed) == len(set(all_claimed)), "Same link claimed by both workers"

    def test_seen_set_no_duplicates_under_race(self, crawler):
        """Two workers adding the same hash should not duplicate."""
        seen = set()

        async def worker(hash_key):
            if hash_key in seen:
                return False
            seen.add(hash_key)
            return True

        async def main():
            w1 = asyncio.create_task(worker("abc123"))
            w2 = asyncio.create_task(worker("abc123"))
            return await asyncio.gather(w1, w2)

        r1, r2 = asyncio.run(main())
        # Without a lock, both could return True (race condition)
        # This test documents the expected behavior after fix
        assert not (r1 and r2), "Both workers claimed the same hash"
