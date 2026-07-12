"""MagnetCrawler lifecycle and Scrapling Spider construction."""

from __future__ import annotations

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler


class FakeCookieProvider:
    def browser_cookies(self):
        return [{"name": "sid", "value": "abc", "domain": "example.com", "path": "/"}]


@pytest.mark.asyncio
async def test_crawler_start_stop_marks_adapter_lifecycle():
    crawler = MagnetCrawler(config=CrawlerConfig())

    await crawler.start()
    assert crawler._started is True

    await crawler.stop()
    assert crawler._started is False


def test_build_spider_maps_site_cookies_and_depth(monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("magnet_harvester.scrapling_spider.AsyncDynamicSession", FakeSession)
    crawler = MagnetCrawler(
        config=CrawlerConfig(max_depth=2),
        site_auth=FakeCookieProvider(),
    )

    spider = crawler._build_spider("https://example.com", depth=9)

    assert spider.depth == 2
    assert captured["cookies"][0]["name"] == "sid"
    assert captured["additional_args"]["service_workers"] == "block"


def test_crawler_falls_back_to_settings_when_no_config_given():
    crawler = MagnetCrawler()
    reference = CrawlerConfig()

    assert crawler._config.timeout == reference.timeout
    assert crawler._config.max_depth == reference.max_depth
    assert crawler._config.concurrency == reference.concurrency
    assert crawler._config.allowed_resolutions == ("2160p", "4k")
