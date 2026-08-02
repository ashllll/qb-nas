"""Scrapling Spider owns crawl scheduling for Magnet Harvester."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from scrapling.fetchers import FetcherSession
from scrapling.spiders import Request

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.scrapling_spider import MagnetSpider
from magnet_harvester.utils.url_validator import CrawlTargetAdmission


async def public_resolver(_hostname: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


async def no_redirect(_url: str) -> None:
    return None


def admission() -> CrawlTargetAdmission:
    return CrawlTargetAdmission(resolver=public_resolver, redirect_probe=no_redirect)


class FakeLinks:
    def __init__(self, links: list[str]):
        self._links = links

    def getall(self) -> list[str]:
        return list(self._links)


def response(url: str, links: list[str] | None = None, status: int = 200):
    page = SimpleNamespace(
        url=url,
        status=status,
        html_content="<html>page</html>",
        text="page",
        body=b"<html>page</html>",
        encoding="utf-8",
        meta={"depth": 1},
        css=lambda selector: (
            FakeLinks(links or []) if selector == "a::attr(href)" else FakeLinks([])
        ),
    )
    page.follow = lambda follow_url, **kwargs: Request(follow_url, **kwargs)
    return page


def make_spider(**config) -> MagnetSpider:
    return MagnetSpider(
        root_url="https://example.com",
        depth=2,
        config=CrawlerConfig(**config),
        target_admission=admission(),
        cookies=[],
    )


def test_spider_maps_crawler_controls_to_scrapling():
    spider = make_spider(
        concurrency=50,
        max_retries=2,
        check_robots_txt=True,
        delay_before_return_html=0.25,
    )

    assert spider.concurrent_requests == 8
    assert spider.concurrent_requests_per_domain == 8
    assert spider.max_blocked_retries == 2
    assert spider.robots_txt_obey is True
    assert spider.allowed_domains == set()
    assert spider._session_manager.default_session_id == "robots"
    assert isinstance(spider._session_manager.get("robots"), FetcherSession)


def test_start_request_contains_dynamic_fetch_policy():
    async def collect():
        spider = make_spider(delay_before_return_html=0.25)
        return [request async for request in spider.start_requests()]

    requests = asyncio.run(collect())

    assert len(requests) == 1
    request = requests[0]
    assert isinstance(request, Request)
    assert request.sid == "browser"
    assert request.meta == {"depth": 1}
    assert request._session_kwargs["wait"] == 250
    assert callable(request._session_kwargs["page_action"])
    assert callable(request._session_kwargs["page_setup"])


def test_parse_streams_page_and_follows_admitted_detail_links():
    async def collect():
        spider = make_spider(max_detail_links=5)
        page = response(
            "https://example.com",
            [
                "/torrent/1",
                "/search/all",
                "https://other.example/torrent/2",
            ],
        )
        return [result async for result in spider.parse(page)]

    results = asyncio.run(collect())

    assert results[0]["kind"] == "page"
    assert results[0]["url"] == "https://example.com"
    follow = results[1]
    assert isinstance(follow, Request)
    assert follow.url == "https://example.com/torrent/1"
    assert follow.meta == {"depth": 2}


def test_parse_respects_scrapling_depth_and_page_limit():
    async def collect_at_limit():
        spider = make_spider(max_detail_links=1)
        page = response("https://example.com", ["/torrent/1", "/torrent/2"])
        return [result async for result in spider.parse(page)]

    results = asyncio.run(collect_at_limit())
    requests = [item for item in results if isinstance(item, Request)]

    assert [item.url for item in requests] == ["https://example.com/torrent/1"]


def test_parse_rejects_final_response_url_outside_allowed_site():
    async def collect():
        spider = make_spider()
        results = [item async for item in spider.parse(response("https://other.example/torrent/1"))]
        return spider, results

    spider, results = asyncio.run(collect())

    assert results == []
    assert spider.errors[0]["url"] == "https://other.example/torrent/1"


def test_blocked_response_records_error_after_scrapling_retries_are_exhausted():
    spider = make_spider(max_retries=1)
    blocked = response("https://example.com", status=403)

    assert asyncio.run(spider.is_blocked(blocked)) is True
    assert spider.errors == []
    assert asyncio.run(spider.is_blocked(blocked)) is True
    assert spider.errors == [{"url": "https://example.com", "message": "HTTP 403，阻断重试已耗尽"}]


def test_successful_response_resets_blocked_retry_tracking():
    spider = make_spider(max_retries=1)
    blocked = response("https://example.com", status=403)
    successful = response("https://example.com", status=200)

    assert asyncio.run(spider.is_blocked(blocked)) is True
    assert asyncio.run(spider.is_blocked(successful)) is False
    assert asyncio.run(spider.is_blocked(blocked)) is True
    assert spider.errors == []


def test_stream_runs_root_and_detail_through_scrapling_scheduler(monkeypatch):
    calls = []

    class FakeDynamicSession:
        def __init__(self, **_kwargs):
            self._is_alive = False

        async def __aenter__(self):
            self._is_alive = True
            return self

        async def __aexit__(self, *_args):
            self._is_alive = False

        async def fetch(self, url, **_kwargs):
            calls.append(url)
            links = ["/torrent/1"] if url == "https://example.com" else []
            return response(url, links=links)

    monkeypatch.setattr(
        "magnet_harvester.scrapling_spider.AsyncDynamicSession",
        FakeDynamicSession,
    )

    async def collect():
        spider = make_spider(max_detail_links=5)
        return [item async for item in spider.stream()]

    items = asyncio.run(collect())

    assert calls == ["https://example.com", "https://example.com/torrent/1"]
    assert [item["url"] for item in items] == calls


def test_browser_route_aborts_private_subresources_and_allows_public_requests():
    async def private_aware_resolver(hostname: str, _port: int) -> list[str]:
        if hostname == "internal.example":
            return ["127.0.0.1"]
        return ["93.184.216.34"]

    class FakePage:
        async def route(self, pattern, handler):
            self.pattern = pattern
            self.handler = handler

        async def route_web_socket(self, pattern, handler):
            self.ws_pattern = pattern
            self.ws_handler = handler

    class FakeRoute:
        def __init__(self, url):
            self.request = SimpleNamespace(url=url)
            self.action = None

        async def abort(self):
            self.action = "abort"

        async def continue_(self):
            self.action = "continue"

    class FakeWebSocketRoute:
        def __init__(self, url):
            self.url = url
            self.action = None

        async def close(self):
            self.action = "close"

        def connect_to_server(self):
            self.action = "connect"

    async def check():
        spider = MagnetSpider(
            root_url="https://example.com",
            depth=1,
            config=CrawlerConfig(),
            target_admission=CrawlTargetAdmission(
                resolver=private_aware_resolver,
                redirect_probe=no_redirect,
            ),
            cookies=[],
        )
        page = FakePage()
        await spider._setup_page(page)
        private = FakeRoute("http://internal.example/admin")
        public = FakeRoute("https://cdn.example/asset.js")
        await page.handler(private)
        await page.handler(public)
        private_ws = FakeWebSocketRoute("ws://internal.example/socket")
        public_ws = FakeWebSocketRoute("wss://cdn.example/socket")
        await page.ws_handler(private_ws)
        await page.ws_handler(public_ws)
        return page, private, public, private_ws, public_ws

    page, private, public, private_ws, public_ws = asyncio.run(check())

    assert page.pattern == "**/*"
    assert private.action == "abort"
    assert public.action == "continue"
    assert page.ws_pattern == "**/*"
    assert private_ws.action == "close"
    assert public_ws.action == "connect"


def test_detail_url_admission_runs_concurrently():
    active = 0
    max_active = 0

    async def concurrent_resolver(_hostname: str, _port: int) -> list[str]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ["93.184.216.34"]

    async def collect():
        spider = MagnetSpider(
            root_url="https://example.com",
            depth=2,
            config=CrawlerConfig(concurrency=4, max_detail_links=8),
            target_admission=CrawlTargetAdmission(
                resolver=concurrent_resolver,
                redirect_probe=no_redirect,
            ),
            cookies=[],
        )
        page = response(
            "https://example.com",
            [f"/torrent/{index}" for index in range(8)],
        )
        return [item async for item in spider.parse(page)]

    results = asyncio.run(collect())

    assert len([item for item in results if isinstance(item, Request)]) == 8
    assert max_active > 1


def test_request_errors_are_forwarded_to_realtime_sink():
    received = []

    async def sink(url: str, message: str) -> None:
        received.append((url, message))

    async def check():
        spider = make_spider()
        spider.set_error_sink(sink)
        await spider.on_error(Request("https://example.com/torrent/1"), RuntimeError("boom"))
        return spider

    spider = asyncio.run(check())

    assert received == [("https://example.com/torrent/1", "boom")]
    assert spider.errors == []


def test_concurrent_spiders_have_isolated_loggers():
    first = make_spider()
    second = make_spider()

    assert first.name != second.name
    assert first.logger is not second.logger
    assert first._log_counter in first.logger.handlers
    assert second._log_counter in second.logger.handlers
    assert first.logger.level == logging.WARNING


def test_detail_admission_deduplicates_and_applies_limit_before_dns():
    resolved = []

    async def counting_resolver(hostname: str, _port: int) -> list[str]:
        resolved.append(hostname)
        return ["93.184.216.34"]

    async def collect():
        spider = MagnetSpider(
            root_url="https://example.com",
            depth=2,
            config=CrawlerConfig(max_detail_links=2),
            target_admission=CrawlTargetAdmission(
                resolver=counting_resolver,
                redirect_probe=no_redirect,
            ),
            cookies=[],
        )
        page = response(
            "https://example.com",
            ["/torrent/1", "/torrent/1", "/torrent/2", "/torrent/3"] * 100,
        )
        return [item async for item in spider.parse(page)]

    results = asyncio.run(collect())

    assert len([item for item in results if isinstance(item, Request)]) == 2
    assert len(resolved) == 3  # final response + two bounded detail candidates


def test_unsafe_early_candidates_do_not_consume_detail_capacity():
    resolve_count = 0

    async def selective_resolver(_hostname: str, _port: int) -> list[str]:
        nonlocal resolve_count
        resolve_count += 1
        if resolve_count == 2:  # root response is first; first detail is rejected
            return ["127.0.0.1"]
        return ["93.184.216.34"]

    async def collect():
        spider = MagnetSpider(
            root_url="https://example.com",
            depth=2,
            config=CrawlerConfig(concurrency=1, max_detail_links=2),
            target_admission=CrawlTargetAdmission(
                resolver=selective_resolver,
                redirect_probe=no_redirect,
            ),
            cookies=[],
        )
        page = response(
            "https://example.com",
            [
                "/torrent/1",
                "/torrent/2",
                "/torrent/3",
            ],
        )
        return [item async for item in spider.parse(page)]

    results = asyncio.run(collect())

    assert [item.url for item in results if isinstance(item, Request)] == [
        "https://example.com/torrent/2",
        "https://example.com/torrent/3",
    ]


def test_scrapling_scheduler_follows_details_for_root_with_explicit_port(monkeypatch):
    calls = []

    class FakeDynamicSession:
        def __init__(self, **_kwargs):
            self._is_alive = False

        async def __aenter__(self):
            self._is_alive = True
            return self

        async def __aexit__(self, *_args):
            self._is_alive = False

        async def fetch(self, url, **_kwargs):
            calls.append(url)
            links = ["/torrent/1"] if url == "https://example.com:8443" else []
            return response(url, links=links)

    monkeypatch.setattr(
        "magnet_harvester.scrapling_spider.AsyncDynamicSession",
        FakeDynamicSession,
    )

    async def collect():
        spider = MagnetSpider(
            root_url="https://example.com:8443",
            depth=2,
            config=CrawlerConfig(max_detail_links=5),
            target_admission=admission(),
            cookies=[],
        )
        return [item async for item in spider.stream()]

    asyncio.run(collect())

    assert calls == [
        "https://example.com:8443",
        "https://example.com:8443/torrent/1",
    ]


def test_completed_spider_releases_unique_logger_from_registry(monkeypatch):
    class FakeDynamicSession:
        def __init__(self, **_kwargs):
            self._is_alive = False

        async def __aenter__(self):
            self._is_alive = True
            return self

        async def __aexit__(self, *_args):
            self._is_alive = False

        async def fetch(self, url, **_kwargs):
            return response(url)

    monkeypatch.setattr(
        "magnet_harvester.scrapling_spider.AsyncDynamicSession",
        FakeDynamicSession,
    )

    async def run():
        spider = make_spider()
        logger_name = spider.logger.name
        _ = [item async for item in spider.stream()]
        return logger_name

    logger_name = asyncio.run(run())

    assert logger_name not in logging.Logger.manager.loggerDict
    assert not any(
        any(logger.name == logger_name for logger in getattr(entry, "loggerMap", {}))
        for entry in logging.Logger.manager.loggerDict.values()
    )


def test_session_start_failure_still_releases_unique_logger(monkeypatch):
    class FailingDynamicSession:
        def __init__(self, **_kwargs):
            self._is_alive = False

        async def __aenter__(self):
            raise RuntimeError("browser failed to start")

        async def __aexit__(self, *_args):
            self._is_alive = False

    monkeypatch.setattr(
        "magnet_harvester.scrapling_spider.AsyncDynamicSession",
        FailingDynamicSession,
    )

    async def run():
        spider = make_spider()
        logger_name = spider.logger.name
        try:
            _ = [item async for item in spider.stream()]
        except BaseException:
            pass
        return logger_name

    logger_name = asyncio.run(run())

    assert logger_name not in logging.Logger.manager.loggerDict
