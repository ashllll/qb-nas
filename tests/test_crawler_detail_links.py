"""
测试详情页链接发现策略
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler


def test_extract_detail_links_filters_and_limits():
    crawler = MagnetCrawler(config=CrawlerConfig())
    visited = {"https://example.com/details/old"}
    links = {
        "internal": [
            {"href": "https://example.com/details/123"},
            {"href": "https://example.com/list?page=2"},
            {"href": "https://example.com/view/abc"},
            {"href": "https://other.com/details/999"},
            {"href": "https://example.com/details/old"},
            {"href": "https://example.com/item?tid=42"},
        ]
    }

    result = asyncio.run(crawler._claim_unvisited_links(
        crawler._extract_detail_links("https://example.com/list", links),
        visited,
    ))

    assert "https://example.com/details/123" in result
    assert "https://example.com/view/abc" in result
    assert "https://example.com/item?tid=42" in result
    assert "https://example.com/list?page=2" not in result
    assert "https://other.com/details/999" not in result
    assert "https://example.com/details/old" not in result


def test_claim_unvisited_links_reserves_before_await_points():
    crawler = MagnetCrawler(config=CrawlerConfig())
    visited = set()
    links = ["https://example.com/details/123"]

    first_claim = asyncio.run(crawler._claim_unvisited_links(links, visited))
    second_claim = asyncio.run(crawler._claim_unvisited_links(links, visited))

    assert first_claim == ["https://example.com/details/123"]
    assert second_claim == []


def test_crawl_worker_reports_page_errors_and_finishes():
    class ExplodingCrawler(MagnetCrawler):
        async def start(self):
            self._crawler = object()

        async def _crawl_page(self, url, depth, visited, frontier, events, seen):
            raise RuntimeError("boom")

    async def collect():
        crawler = ExplodingCrawler(config=CrawlerConfig(concurrency=1))
        messages = []
        async with asyncio.timeout(1):
            async for message in crawler.crawl("https://example.com", depth=1):
                messages.append(message)
        return messages

    messages = asyncio.run(collect())

    assert any(message["type"] == "error" and message["msg"] == "boom" for message in messages)
    assert messages[-1]["type"] == "done"
    assert messages[-1]["metrics"]["errors"] == 1


def test_crawl_consumer_close_cleans_up_worker_session():
    class SlowCrawler(MagnetCrawler):
        async def start(self):
            self._crawler = object()

        async def _crawl_page(self, url, depth, visited, frontier, events, seen):
            await events.put({"type": "progress", "msg": "tick", "url": url})
            await asyncio.sleep(0.02)

    async def consume_and_close():
        crawler = SlowCrawler(config=CrawlerConfig(concurrency=2))
        stream = crawler.crawl("https://example.com", depth=1)
        first = await anext(stream)
        assert first["type"] == "progress"
        async with asyncio.timeout(1):
            await stream.aclose()

    asyncio.run(consume_and_close())


if __name__ == "__main__":
    test_extract_detail_links_filters_and_limits()
    test_claim_unvisited_links_reserves_before_await_points()
    test_crawl_worker_reports_page_errors_and_finishes()
    test_crawl_consumer_close_cleans_up_worker_session()
    print("=== crawler detail link tests passed! ===")
