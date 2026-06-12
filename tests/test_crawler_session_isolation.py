"""Concurrent Crawl sessions keep independent mutable state."""
import asyncio

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.utils.url_validator import CrawlTargetAdmission


async def public_resolver(_hostname, _port):
    return ["93.184.216.34"]


async def no_redirect(_url):
    return None


class OverlappingCrawler(MagnetCrawler):
    async def start(self):
        self._crawler = object()

    async def _crawl_page(self, url, depth, visited, frontier, events, seen):
        metrics = self._current_metrics()
        metrics.pages_crawled += 1
        await asyncio.sleep(0.02 if "first" in url else 0.01)
        metrics.magnets_found += 1 if "first" in url else 2


@pytest.mark.asyncio
async def test_overlapping_crawl_sessions_report_independent_metrics():
    crawler = OverlappingCrawler(
        config=CrawlerConfig(concurrency=1),
        target_admission=CrawlTargetAdmission(
            resolver=public_resolver,
            redirect_probe=no_redirect,
        ),
    )

    async def collect(url):
        return [message async for message in crawler.crawl(url)]

    first, second = await asyncio.gather(
        collect("https://first.example"),
        collect("https://second.example"),
    )

    first_done = first[-1]
    second_done = second[-1]
    assert first_done["total"] == 1
    assert second_done["total"] == 2
    assert first_done["metrics"]["pages_crawled"] == 1
    assert second_done["metrics"]["pages_crawled"] == 1
