"""
集成测试：MagnetCrawler (Scrapling 适配器) 生命周期
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.config import CrawlerConfig


class FakeCookieProvider:
    def __init__(self, cookies):
        self.cookies = cookies

    def browser_cookies(self):
        return list(self.cookies)


@pytest.mark.asyncio
async def test_crawler_start_stop(monkeypatch):
    """验证 crawler 使用 Scrapling 能正常启动和关闭"""

    class FakeAsyncDynamicSession:
        def __init__(self, **_kwargs):
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

    monkeypatch.setattr("magnet_harvester.crawler.AsyncDynamicSession", FakeAsyncDynamicSession)

    config = CrawlerConfig(headless=True, timeout=10)
    crawler = MagnetCrawler(config=config)

    try:
        # 启动
        await crawler.start()
        # 确认已启动（没有异常即可）
        assert crawler._crawler is not None, "Scrapling AsyncDynamicSession 应已创建"
    finally:
        # 关闭
        await crawler.stop()

    assert crawler._crawler is None, "关闭后 _crawler 应为 None"


@pytest.mark.asyncio
async def test_crawler_start_uses_injected_site_cookies(monkeypatch):
    """Crawler startup should get browser cookies from the SiteAuth seam."""
    captured = {}

    class FakeAsyncDynamicSession:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        async def __aenter__(self):
            captured["started"] = True
            return self

        async def __aexit__(self, *_args):
            captured["closed"] = True

    cookies = [{"name": "sid", "value": "abc", "domain": "example.com", "path": "/"}]
    monkeypatch.setattr("magnet_harvester.crawler.AsyncDynamicSession", FakeAsyncDynamicSession)

    crawler = MagnetCrawler(
        config=CrawlerConfig(headless=True, timeout=10),
        site_auth=FakeCookieProvider(cookies),
    )

    await crawler.start()
    try:
        assert captured["started"] is True
        assert captured["kwargs"]["cookies"] == cookies
    finally:
        await crawler.stop()


@pytest.mark.asyncio
async def test_crawler_context_manager(monkeypatch):
    """验证 async with 用法（通过 start/stop 模拟）"""

    class FakeAsyncDynamicSession:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr("magnet_harvester.crawler.AsyncDynamicSession", FakeAsyncDynamicSession)

    config = CrawlerConfig(headless=True, timeout=10)
    crawler = MagnetCrawler(config=config)

    # 启动前不能 crawl
    assert crawler._crawler is None

    await crawler.start()
    try:
        assert crawler._crawler is not None
    finally:
        await crawler.stop()

    assert crawler._crawler is None


def test_crawler_falls_back_to_settings_when_no_config_given(monkeypatch):
    """MagnetCrawler() 无参构造时应使用 settings.crawler 作为默认配置。"""
    from magnet_harvester.config import CrawlerConfig

    crawler = MagnetCrawler()
    default = crawler._config

    # 验证关键字段与默认 CrawlerConfig 一致
    ref = CrawlerConfig()
    assert default.timeout == ref.timeout
    assert default.max_depth == ref.max_depth
    assert default.concurrency == ref.concurrency
    assert default.headless == ref.headless
    assert default.allowed_resolutions == ("2160p", "4k")
    assert default.wait_until == ref.wait_until
    assert default.delay_before_return_html == ref.delay_before_return_html
    assert default.word_count_threshold == ref.word_count_threshold
    assert default.scan_full_page == ref.scan_full_page
    assert default.max_retries == ref.max_retries
    assert default.check_robots_txt == ref.check_robots_txt
    assert default.simulate_user == ref.simulate_user
    assert default.magics == ref.magics
    assert default.url_score_depth_bias == ref.url_score_depth_bias


if __name__ == "__main__":
    import asyncio

    async def run():
        config = CrawlerConfig(headless=True, timeout=10)
        crawler = MagnetCrawler(config=config)
        print("正在启动 Scrapling 爬虫...")
        await crawler.start()
        print("爬虫已启动 ✓")
        print(f"  AsyncDynamicSession: {crawler._crawler}")
        await crawler.stop()
        print("爬虫已关闭 ✓")
        print("生命周期测试通过!")

    asyncio.run(run())
