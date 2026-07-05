"""
重试/批量调度交给 Scrapling 配置处理。
"""

import asyncio

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler


def test_start_passes_concurrency_to_scrapling_session(monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    crawler = MagnetCrawler(config=CrawlerConfig(concurrency=7, max_retries=3))

    monkeypatch.setattr("magnet_harvester.crawler.AsyncDynamicSession", FakeSession)

    asyncio.run(crawler.start())
    asyncio.run(crawler.stop())

    assert captured["max_pages"] == 7
    assert captured["retries"] == 4


def test_fetch_options_use_configured_timeout_without_ignored_retry_kwarg():
    captured = {}

    class FakeSession:
        async def fetch(self, url, **kwargs):
            captured.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "url": url,
                    "status": 200,
                    "html_content": "",
                    "text": "",
                    "body": b"",
                    "encoding": "utf-8",
                },
            )()

    async def check():
        crawler = MagnetCrawler(config=CrawlerConfig(timeout=12, max_retries=3))
        await crawler._fetch_page(FakeSession(), "https://example.com")

    asyncio.run(check())

    assert captured["timeout"] == 12000
    assert "retries" not in captured
    assert callable(captured["page_action"])


def test_dynamic_page_action_uses_crawler_page_options():
    class FakePage:
        def __init__(self):
            self.evaluations = []
            self.waits = []

        async def evaluate(self, script, arg=None):
            self.evaluations.append((script, arg))

        async def wait_for_timeout(self, timeout):
            self.waits.append(timeout)

    async def check():
        crawler = MagnetCrawler(
            config=CrawlerConfig(
                scan_full_page=True,
                max_scroll_steps=2,
                scroll_delay=0.05,
                process_iframes=True,
                flatten_shadow_dom=True,
                remove_overlay_elements=True,
                remove_consent_popups=False,
            )
        )
        page = FakePage()
        await crawler._prepare_dynamic_page(page)
        return page

    page = asyncio.run(check())

    assert len(page.waits) == 2
    assert page.waits == [50, 50]
    assert page.evaluations[0][1] == {"removeOverlays": True, "removeConsent": False}
    assert page.evaluations[-1][1] == {"processIframes": True, "flattenShadowDom": True}
    assert "node.querySelectorAll?.(\"*\").forEach(walk)" not in page.evaluations[-1][0]
    assert "const stack = [...document.querySelectorAll(\"*\")]" in page.evaluations[-1][0]


def test_worker_count_is_capped_for_browser_sessions():
    crawler = MagnetCrawler(config=CrawlerConfig(concurrency=50))

    assert crawler._worker_count == 8


if __name__ == "__main__":
    print("=== Scrapling retry/deep crawl config tests passed! ===")
