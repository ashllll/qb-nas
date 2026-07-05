"""
测试 Scrapling 爬虫详情页遍历。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.utils.url_validator import CrawlTargetAdmission


async def public_resolver(_hostname, _port):
    return ["93.184.216.34"]


async def private_resolver(_hostname, _port):
    return ["127.0.0.1"]


async def no_redirect(_url):
    return None


def make_crawler(**config):
    return MagnetCrawler(
        config=CrawlerConfig(**config),
        target_admission=CrawlTargetAdmission(
            resolver=public_resolver,
            redirect_probe=no_redirect,
        ),
    )


def page(url, html="", status=200):
    return SimpleNamespace(
        url=url,
        status=status,
        html_content=html,
        text=html,
        body=html.encode(),
        encoding="utf-8",
    )


def test_clamp_depth_respects_config_max():
    crawler = make_crawler(max_depth=2)

    assert crawler._clamp_depth(0) == 1
    assert crawler._clamp_depth(1) == 1
    assert crawler._clamp_depth(2) == 2
    assert crawler._clamp_depth(3) == 2
    assert crawler._clamp_depth(5) == 2


def test_discover_detail_links_keeps_detail_urls_and_rejects_listing_urls():
    crawler = make_crawler()
    result = SimpleNamespace(
        url="https://example.com/torrents",
        success=True,
        html="""
            <a href="/torrents/details/123">detail</a>
            <a href="/item?tid=42">item</a>
            <a href="/torrents/search/all">listing</a>
            <a href="https://other.example/torrent/9">external</a>
        """,
        cleaned_html="",
        markdown="",
    )

    async def check():
        links = await crawler._discover_detail_links(result, "https://example.com")
        assert links == [
            "https://example.com/torrents/details/123",
            "https://example.com/item?tid=42",
        ]

    asyncio.run(check())


def test_same_site_allows_explicit_default_port():
    assert MagnetCrawler._is_same_site(
        "https://example.com:443/torrents/details/123",
        "https://example.com",
    )


def test_discover_detail_links_applies_project_url_admission():
    crawler = MagnetCrawler(
        config=CrawlerConfig(),
        target_admission=CrawlTargetAdmission(
            resolver=private_resolver,
            redirect_probe=no_redirect,
        ),
    )
    result = SimpleNamespace(
        url="https://example.com",
        success=True,
        html='<a href="/torrents/details/123">detail</a>',
        cleaned_html="",
        markdown="",
    )

    async def check():
        assert await crawler._discover_detail_links(result, "https://example.com") == []

    asyncio.run(check())


async def redirect_to_private(_url):
    return "http://192.168.1.10/torrent/secret"


async def redirect_to_external_public(_url):
    return "https://other.example/torrent/secret"


def test_discover_detail_links_rejects_public_url_that_redirects_to_private():
    crawler = MagnetCrawler(
        config=CrawlerConfig(),
        target_admission=CrawlTargetAdmission(
            resolver=public_resolver,
            redirect_probe=redirect_to_private,
        ),
    )
    result = SimpleNamespace(
        url="https://example.com",
        success=True,
        html='<a href="/torrents/details/123">detail</a>',
        cleaned_html="",
        markdown="",
    )

    async def check():
        assert await crawler._discover_detail_links(result, "https://example.com") == []

    asyncio.run(check())


def test_discover_detail_links_rejects_public_url_that_redirects_external():
    crawler = MagnetCrawler(
        config=CrawlerConfig(),
        target_admission=CrawlTargetAdmission(
            resolver=public_resolver,
            redirect_probe=redirect_to_external_public,
        ),
    )
    result = SimpleNamespace(
        url="https://example.com",
        success=True,
        html='<a href="/torrents/details/123">detail</a>',
        cleaned_html="",
        markdown="",
    )

    async def check():
        assert await crawler._discover_detail_links(result, "https://example.com") == []

    asyncio.run(check())


def test_fetch_deep_stream_uses_scrapling_session_for_root_and_detail_links():
    class FakeSession:
        def __init__(self):
            self.calls = []

        async def fetch(self, url, **_kwargs):
            self.calls.append(url)
            if url == "https://example.com":
                return page(
                    url,
                    """
                    <a href="/torrents/details/123">detail</a>
                    <a href="/torrents/search/all">listing</a>
                    <a href="https://other.example/torrent/9">external</a>
                    """,
                )
            return page(url, "magnet:?xt=urn:btih:" + "1" * 40 + "&dn=Movie.2160p")

    async def collect():
        crawler = make_crawler(max_detail_links=5)
        fake = FakeSession()
        crawler._crawler = fake
        results = [result async for result in crawler._fetch_deep_stream("https://example.com", 2)]
        return fake, results

    fake, results = asyncio.run(collect())

    assert fake.calls == ["https://example.com", "https://example.com/torrents/details/123"]
    assert [result.url for result in results] == fake.calls


def test_fetch_deep_stream_respects_page_limit():
    class FakeSession:
        async def fetch(self, url, **_kwargs):
            if url == "https://example.com":
                return page(
                    url,
                    """
                    <a href="/torrent/1">one</a>
                    <a href="/torrent/2">two</a>
                    """,
                )
            return page(url)

    async def collect():
        crawler = make_crawler(max_detail_links=1)
        crawler._crawler = FakeSession()
        return [result async for result in crawler._fetch_deep_stream("https://example.com", 2)]

    results = asyncio.run(collect())

    assert [result.url for result in results] == ["https://example.com", "https://example.com/torrent/1"]


def test_crawl_progress_reports_effective_depth():
    class CapturingCrawler(MagnetCrawler):
        async def start(self):
            self._crawler = object()

        async def _fetch_deep_stream(self, root_url, depth):
            self.captured_depth = depth
            yield SimpleNamespace(
                url=root_url,
                success=True,
                markdown="",
                cleaned_html="",
                html="",
            )

    async def collect():
        crawler = CapturingCrawler(
            config=CrawlerConfig(max_depth=2, concurrency=1),
            target_admission=CrawlTargetAdmission(
                resolver=public_resolver,
                redirect_probe=no_redirect,
            ),
        )
        messages = []
        async with asyncio.timeout(1):
            async for message in crawler.crawl("https://example.com", depth=5):
                messages.append(message)
        return crawler, messages

    crawler, messages = asyncio.run(collect())

    assert crawler.captured_depth == 2
    progress = next(m for m in messages if m["type"] == "progress")
    assert progress["depth"] == 2


def test_crawl_batch_reports_page_errors_and_finishes():
    class ExplodingCrawler(MagnetCrawler):
        async def start(self):
            self._crawler = object()

        async def _fetch_deep_stream(self, root_url, depth):
            yield SimpleNamespace(
                url=root_url,
                success=False,
                error_message="boom",
                markdown="",
                cleaned_html="",
                html="",
            )

    async def collect():
        crawler = ExplodingCrawler(
            config=CrawlerConfig(concurrency=1),
            target_admission=CrawlTargetAdmission(
                resolver=public_resolver,
                redirect_probe=no_redirect,
            ),
        )
        messages = []
        async with asyncio.timeout(1):
            async for message in crawler.crawl("https://example.com", depth=1):
                messages.append(message)
        return messages

    messages = asyncio.run(collect())

    assert any(message["type"] == "error" and message["msg"] == "boom" for message in messages)
    assert messages[-1]["type"] == "done"
    assert messages[-1]["metrics"]["errors"] == 1


def test_crawl_yields_error_and_done_when_fetch_deep_stream_raises():
    class ExplodingFetchCrawler(MagnetCrawler):
        async def start(self):
            self._crawler = object()

        async def _fetch_deep_stream(self, root_url, depth):
            raise RuntimeError("deep fetch exploded")
            yield

    async def collect():
        crawler = ExplodingFetchCrawler(
            config=CrawlerConfig(concurrency=1),
            target_admission=CrawlTargetAdmission(
                resolver=public_resolver,
                redirect_probe=no_redirect,
            ),
        )
        messages = []
        async with asyncio.timeout(1):
            async for message in crawler.crawl("https://example.com", depth=1):
                messages.append(message)
        return messages

    messages = asyncio.run(collect())

    assert any(
        message["type"] == "error" and "deep fetch exploded" in message["msg"]
        for message in messages
    )
    assert messages[-1]["type"] == "done"
    assert messages[-1]["metrics"]["errors"] == 1


def test_crawl_consumer_close_cleans_up_deep_crawl_session():
    class SlowCrawler(MagnetCrawler):
        async def start(self):
            self._crawler = object()

        async def _fetch_deep_stream(self, root_url, depth):
            await asyncio.sleep(0.02)
            yield SimpleNamespace(
                url=root_url,
                success=True,
                markdown="",
                cleaned_html="",
                html="",
            )

    async def consume_and_close():
        crawler = SlowCrawler(
            config=CrawlerConfig(concurrency=2),
            target_admission=CrawlTargetAdmission(
                resolver=public_resolver,
                redirect_probe=no_redirect,
            ),
        )
        stream = crawler.crawl("https://example.com", depth=1)
        first = await anext(stream)
        assert first["type"] == "progress"
        async with asyncio.timeout(1):
            await stream.aclose()

    asyncio.run(consume_and_close())


def test_crawl_consumer_close_cancels_unfinished_session():
    class HangingCrawler(MagnetCrawler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.cancelled = False

        async def start(self):
            self._crawler = object()

        async def _fetch_deep_stream(self, root_url, depth):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            yield

    async def consume_and_close():
        crawler = HangingCrawler(
            config=CrawlerConfig(concurrency=2),
            target_admission=CrawlTargetAdmission(
                resolver=public_resolver,
                redirect_probe=no_redirect,
            ),
        )
        stream = crawler.crawl("https://example.com", depth=1)
        first = await anext(stream)
        assert first["type"] == "progress"
        async with asyncio.timeout(0.2):
            await stream.aclose()
        assert crawler.cancelled is True

    asyncio.run(consume_and_close())
