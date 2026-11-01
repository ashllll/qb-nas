"""MagnetCrawler 适配层：生命周期、深度夹取、默认策略。"""

from __future__ import annotations

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler


class FakeCookieProvider:
    def browser_cookies(self) -> list[dict]:
        return []


class StubFetcher:
    def __init__(self) -> None:
        self.started = 0
        self.closed = 0

    async def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        self.closed += 1

    async def fetch(self, url, *, allow_browser=True):  # pragma: no cover
        raise AssertionError("adapter tests do not fetch")


class StubAdmission:
    async def admit(self, url: str) -> str:
        return url

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_start_stop_delegate_to_fetcher():
    fetcher = StubFetcher()
    crawler = MagnetCrawler(
        config=CrawlerConfig(),
        target_admission=StubAdmission(),
        site_auth=FakeCookieProvider(),
        fetcher=fetcher,
    )
    await crawler.start()
    assert crawler._started is True
    assert fetcher.started == 1
    await crawler.stop()
    assert crawler._started is False
    assert fetcher.closed == 1


def test_depth_is_clamped_to_config_max():
    crawler = MagnetCrawler(
        config=CrawlerConfig(max_depth=2),
        target_admission=StubAdmission(),
        site_auth=FakeCookieProvider(),
        fetcher=StubFetcher(),
    )
    assert crawler._clamp_depth(1) == 1
    assert crawler._clamp_depth(9) == 2
    assert crawler._clamp_depth(0) == 1


def test_walker_receives_root_url_and_depth():
    crawler = MagnetCrawler(
        config=CrawlerConfig(max_depth=2),
        target_admission=StubAdmission(),
        site_auth=FakeCookieProvider(),
        fetcher=StubFetcher(),
    )
    walker = crawler._build_walker("https://site.test/list", 2)
    assert walker.root_url == "https://site.test/list"
    assert walker.depth == 2


def test_crawler_falls_back_to_settings_when_no_config_given():
    crawler = MagnetCrawler()
    reference = CrawlerConfig()

    assert crawler._config.timeout == reference.timeout
    assert crawler._config.max_depth == reference.max_depth
    assert crawler._config.concurrency == reference.concurrency
    assert crawler._config.allowed_resolutions == ()


def test_default_config_collects_every_resolution():
    """默认不做分辨率过滤：1080p/720p 也是有效资源（产品要求「多条全收」）。"""
    assert CrawlerConfig().allowed_resolutions == ()
