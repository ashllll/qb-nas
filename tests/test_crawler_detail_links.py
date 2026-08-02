"""MagnetCrawler adapts Scrapling Spider output to public crawl events."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import CrawlMetrics, MagnetCrawler
from magnet_harvester.utils.url_validator import CrawlTargetAdmission


async def public_resolver(_hostname: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


async def no_redirect(_url: str) -> None:
    return None


class FakeSpider:
    def __init__(self, items=None, errors=None):
        self.items = list(items or [])
        self.errors = list(errors or [])
        self.cancelled = False
        self.error_sink = None

    def set_error_sink(self, sink):
        self.error_sink = sink

    async def stream(self):
        try:
            for item in self.items:
                yield item
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class SpiderCrawler(MagnetCrawler):
    def __init__(self, spider: FakeSpider, **kwargs):
        super().__init__(**kwargs)
        self.spider = spider
        self.requested_depth = None

    def _build_spider(self, root_url: str, depth: int):
        self.requested_depth = depth
        return self.spider


def make_crawler(spider: FakeSpider, **config) -> SpiderCrawler:
    return SpiderCrawler(
        spider,
        config=CrawlerConfig(**config),
        target_admission=CrawlTargetAdmission(
            resolver=public_resolver,
            redirect_probe=no_redirect,
        ),
    )


def page_item(url="https://example.com", html="", success=True, error_message=""):
    return {
        "kind": "page",
        "url": url,
        "success": success,
        "html": html,
        "cleaned_html": html,
        "markdown": html,
        "error_message": error_message,
    }


def test_crawl_elapsed_time_uses_monotonic_clock(monkeypatch):
    readings = iter([100.0, 101.5])
    monkeypatch.setattr(
        "magnet_harvester.crawler.time.monotonic",
        lambda: next(readings),
    )
    monkeypatch.setattr(
        "magnet_harvester.crawler.time.time",
        lambda: -9999.0,
    )

    metrics = CrawlMetrics()

    assert metrics.elapsed == 1.5


def test_crawl_streams_spider_pages_as_found_and_done_events():
    magnet = "magnet:?xt=urn:btih:" + "1" * 40 + "&dn=Movie.2160p"
    crawler = make_crawler(FakeSpider([page_item(html=magnet)]), max_depth=2)

    async def collect():
        return [event async for event in crawler.crawl("https://example.com", depth=9)]

    events = asyncio.run(collect())

    assert crawler.requested_depth == 2
    assert any(event["type"] == "found" for event in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["total"] == 1
    assert events[-1]["metrics"]["pages_crawled"] == 1


def test_crawl_reports_page_and_spider_errors_then_finishes():
    spider = FakeSpider(
        [page_item(success=False, error_message="HTTP 404")],
        errors=[{"url": "https://example.com/torrent/2", "message": "network failed"}],
    )
    crawler = make_crawler(spider)

    async def collect():
        return [event async for event in crawler.crawl("https://example.com")]

    events = asyncio.run(collect())
    errors = [event for event in events if event["type"] == "error"]

    assert [event["msg"] for event in errors] == ["HTTP 404", "network failed"]
    assert events[-1]["type"] == "done"
    assert events[-1]["metrics"]["errors"] == 2


def test_crawl_forwards_spider_errors_before_stream_completion():
    class RealtimeErrorSpider(FakeSpider):
        async def stream(self):
            await self.error_sink("https://example.com/torrent/1", "network failed")
            yield page_item()

    crawler = make_crawler(RealtimeErrorSpider())

    async def collect():
        return [event async for event in crawler.crawl("https://example.com")]

    events = asyncio.run(collect())

    error_index = next(i for i, event in enumerate(events) if event["type"] == "error")
    progress_index = next(
        i for i, event in enumerate(events) if event["type"] == "progress" and "metrics" in event
    )
    assert error_index < progress_index
    assert events[-1]["metrics"]["errors"] == 1


def test_crawl_event_queue_is_bounded_for_consumer_backpressure():
    crawler = make_crawler(FakeSpider(), concurrency=8)

    queue = crawler._make_event_queue()

    assert queue.maxsize == 64


def test_timeout_preserves_error_then_done_event_contract():
    crawler = make_crawler(FakeSpider())

    async def immediate_timeout(awaitable, timeout):
        del timeout
        awaitable.close()
        raise asyncio.TimeoutError

    async def collect():
        with patch(
            "magnet_harvester.crawler.asyncio.wait_for",
            new=immediate_timeout,
        ):
            return [event async for event in crawler.crawl("https://example.com")]

    events = asyncio.run(collect())

    assert [event["type"] for event in events] == ["error", "done"]
    assert events[-1]["url"] == "https://example.com"


def test_crawl_consumer_close_cancels_scrapling_stream():
    class HangingSpider(FakeSpider):
        async def stream(self):
            try:
                await asyncio.Event().wait()
                yield {}
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    spider = HangingSpider()
    crawler = make_crawler(spider)

    async def consume_and_close():
        stream = crawler.crawl("https://example.com")
        assert (await anext(stream))["type"] == "progress"
        async with asyncio.timeout(1):
            await stream.aclose()

    asyncio.run(consume_and_close())

    assert spider.cancelled is True


def test_consumer_close_does_not_deadlock_when_bounded_queue_is_full():
    class FloodingSpider(FakeSpider):
        async def stream(self):
            for index in range(100):
                await self.error_sink(f"https://example.com/{index}", "failed")
            yield page_item()

    crawler = make_crawler(FloodingSpider(), concurrency=1)

    async def consume_and_close():
        stream = crawler.crawl("https://example.com")
        assert (await anext(stream))["type"] == "progress"
        await asyncio.sleep(0)
        async with asyncio.timeout(1):
            await stream.aclose()

    asyncio.run(consume_and_close())
