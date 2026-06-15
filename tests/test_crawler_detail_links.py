"""
测试 crawl4ai 深爬策略配置。
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawl4ai import CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

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


def test_build_run_config_uses_crawl4ai_dynamic_page_features():
    crawler = make_crawler()
    strategy = crawler._build_deep_crawl_strategy(depth=2)

    cfg = crawler._build_run_config(stream=True, deep_crawl_strategy=strategy)

    assert cfg.cache_mode == CacheMode.BYPASS
    assert cfg.stream is True
    assert cfg.deep_crawl_strategy is strategy
    assert cfg.semaphore_count == 6
    assert cfg.wait_until == "load"
    assert cfg.delay_before_return_html == 1.0
    assert cfg.scan_full_page is True
    assert cfg.max_scroll_steps == 8
    assert cfg.process_iframes is True
    assert cfg.flatten_shadow_dom is True
    assert cfg.remove_overlay_elements is True
    assert cfg.remove_consent_popups is True


def test_build_deep_crawl_strategy_delegates_depth_and_limits_to_crawl4ai():
    crawler = make_crawler(max_detail_links=120, max_depth=3)

    strategy = crawler._build_deep_crawl_strategy(depth=3)

    assert isinstance(strategy, BFSDeepCrawlStrategy)
    assert strategy.max_depth == 2
    assert strategy.max_pages == 121
    assert strategy.include_external is False


def test_clamp_depth_respects_config_max():
    crawler = make_crawler(max_depth=2)

    assert crawler._clamp_depth(0) == 1
    assert crawler._clamp_depth(1) == 1
    assert crawler._clamp_depth(2) == 2
    assert crawler._clamp_depth(3) == 2
    assert crawler._clamp_depth(5) == 2


def test_build_deep_crawl_strategy_uses_config_max_depth_as_upper_bound():
    crawler = make_crawler(max_depth=2)

    effective_depth = crawler._clamp_depth(5)
    strategy = crawler._build_deep_crawl_strategy(effective_depth)

    assert isinstance(strategy, BFSDeepCrawlStrategy)
    assert strategy.max_depth == 1


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
                links={},
                metadata={"depth": 0},
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


def test_deep_crawl_filter_keeps_detail_urls_and_rejects_listing_urls():
    crawler = make_crawler()
    strategy = crawler._build_deep_crawl_strategy(depth=2)

    async def check():
        assert await strategy.filter_chain.apply("https://example.com/torrents/details/123")
        assert await strategy.filter_chain.apply("https://example.com/item?tid=42")
        assert not await strategy.filter_chain.apply("https://example.com/torrents/search/all")

    asyncio.run(check())


def test_deep_crawl_filter_applies_project_url_admission():
    crawler = MagnetCrawler(
        config=CrawlerConfig(),
        target_admission=CrawlTargetAdmission(
            resolver=private_resolver,
            redirect_probe=no_redirect,
        ),
    )
    strategy = crawler._build_deep_crawl_strategy(depth=2)

    async def check():
        assert not await strategy.filter_chain.apply("https://example.com/torrents/details/123")

    asyncio.run(check())


async def redirect_to_private(_url):
    return "http://192.168.1.10/torrent/secret"


def test_deep_crawl_filter_rejects_public_url_that_redirects_to_private():
    crawler = MagnetCrawler(
        config=CrawlerConfig(),
        target_admission=CrawlTargetAdmission(
            resolver=public_resolver,
            redirect_probe=redirect_to_private,
        ),
    )
    strategy = crawler._build_deep_crawl_strategy(depth=2)

    async def check():
        assert not await strategy.filter_chain.apply("https://example.com/torrents/details/123")

    asyncio.run(check())


def test_fetch_deep_stream_uses_arun_with_streaming_deep_crawl_strategy():
    class FakeCrawl4AI:
        def __init__(self):
            self.calls = []

        async def arun(self, url, config=None):
            self.calls.append((url, config))

            async def stream():
                yield SimpleNamespace(
                    url=url,
                    success=True,
                    markdown="",
                    cleaned_html="",
                    html="",
                    links={},
                    metadata={"depth": 0},
                )

            return stream()

    async def collect():
        crawler = make_crawler()
        fake = FakeCrawl4AI()
        crawler._crawler = fake
        results = [result async for result in crawler._fetch_deep_stream("https://example.com", 2)]
        return fake, results

    fake, results = asyncio.run(collect())

    assert len(results) == 1
    url, config = fake.calls[0]
    assert url == "https://example.com"
    assert config.stream is True
    assert isinstance(config.deep_crawl_strategy, BFSDeepCrawlStrategy)


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
                links={},
                metadata={"depth": 0},
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
            yield  # marks this as an async generator

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
                links={},
                metadata={"depth": 0},
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


if __name__ == "__main__":
    test_build_run_config_uses_crawl4ai_dynamic_page_features()
    test_build_deep_crawl_strategy_delegates_depth_and_limits_to_crawl4ai()
    test_clamp_depth_respects_config_max()
    test_build_deep_crawl_strategy_uses_config_max_depth_as_upper_bound()
    test_crawl_progress_reports_effective_depth()
    test_deep_crawl_filter_keeps_detail_urls_and_rejects_listing_urls()
    test_deep_crawl_filter_applies_project_url_admission()
    test_deep_crawl_filter_rejects_public_url_that_redirects_to_private()
    test_fetch_deep_stream_uses_arun_with_streaming_deep_crawl_strategy()
    test_crawl_batch_reports_page_errors_and_finishes()
    test_crawl_yields_error_and_done_when_fetch_deep_stream_raises()
    test_crawl_consumer_close_cleans_up_deep_crawl_session()
    print("=== crawler deep crawl tests passed! ===")
