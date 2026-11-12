"""取页层（page_fetch）单元测试 — 全部离线，不出网。

覆盖的都是线上会要命的性质：
- HTTP 能拿到就绝不开浏览器；
- 人机校验页 / 空壳页 / 403 才升级，且升级失败要有原因；
- 超时、5xx 会退避重试，404 不会；
- 会话跨调用复用（第二次不再新建）；
- 每站点最小间隔真的生效。
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.page_fetch import (
    FailureReason,
    HostRateLimiter,
    PageFetcher,
    _looks_like_empty_shell,
)
from magnet_harvester.utils.url_validator import CrawlTargetAdmission


class FakeResponse:
    def __init__(
        self,
        status: int,
        html: str,
        url: str = "https://site.test/x",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.html_content = html
        self.text = html
        self.url = url
        self.headers = headers or {}


class FakeHttpSession:
    def __init__(self, responder) -> None:
        self.responder = responder
        self.calls: list[dict] = []

    async def get(self, url: str, timeout: float | None = None, follow_redirects: bool = True):
        self.calls.append({"url": url, "timeout": timeout, "follow_redirects": follow_redirects})
        result = self.responder(url)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Exception):
            raise result
        return result


class FakeHttpCtx:
    instances = 0

    def __init__(self, responder) -> None:
        self._session = FakeHttpSession(responder)
        FakeHttpCtx.instances += 1
        self.closed = False

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        self.closed = True


class FakeBrowser:
    started = 0
    closed = 0

    def __init__(self, responder) -> None:
        self.responder = responder
        self.calls: list[dict] = []

    async def start(self):
        FakeBrowser.started += 1

    async def close(self):
        FakeBrowser.closed += 1

    async def fetch(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        result = self.responder(url)
        if isinstance(result, Exception):
            raise result
        return result


def make_fetcher(http_responder, browser_responder=None, *, sleeps=None, **config_kwargs):
    config = CrawlerConfig(
        timeout=5,
        max_retries=1,
        retry_backoff=0.01,
        per_host_min_interval=0.0,
        **config_kwargs,
    )
    sleeps = sleeps if sleeps is not None else []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    http_ctx_holder: dict[str, FakeHttpCtx] = {}

    def http_factory():
        ctx = FakeHttpCtx(http_responder)
        http_ctx_holder["ctx"] = ctx
        return ctx

    browser_holder: dict[str, FakeBrowser] = {}

    def browser_factory():
        browser = FakeBrowser(
            browser_responder or (lambda url: FakeResponse(200, "<html>ok</html>"))
        )
        browser_holder["browser"] = browser
        return browser

    fetcher = PageFetcher(
        config,
        http_factory=http_factory,
        browser_factory=browser_factory,
        sleep=fake_sleep,
    )
    return fetcher, http_ctx_holder, browser_holder, sleeps


@pytest.mark.asyncio
async def test_http_success_never_touches_browser():
    fetcher, _, browsers, _ = make_fetcher(
        lambda url: FakeResponse(200, "<a href='/details/1'>1</a>")
    )
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok
    assert page.via == "http"
    assert "browser" not in browsers


@pytest.mark.asyncio
async def test_browser_first_skips_http_entirely():
    fetcher, https, browsers, _ = make_fetcher(
        lambda url: FakeResponse(200, "<html>http</html>"),
        lambda url: FakeResponse(200, "<html>rendered</html>"),
    )
    page = await fetcher.fetch("https://site.test/list", browser_first=True)
    assert page.ok and page.via == "browser"
    assert https == {}
    assert browsers["browser"].calls[0]["url"] == "https://site.test/list"


@pytest.mark.asyncio
async def test_challenge_page_escalates_to_browser():
    fetcher, _, browsers, _ = make_fetcher(
        lambda url: FakeResponse(403, "<html>Just a moment...</html>"),
        lambda url: FakeResponse(200, "<html><a href='/details/1'>1</a></html>"),
    )
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok
    assert page.via == "browser"
    assert browsers["browser"].calls[0]["url"] == "https://site.test/list"


@pytest.mark.asyncio
async def test_http_200_but_cloudflare_challenge_counts_as_blocked_and_escalates():
    fetcher, _, _, _ = make_fetcher(
        lambda url: FakeResponse(200, "<html><title>Just a moment...</title></html>"),
        lambda url: FakeResponse(200, "<html>real content here with magnet</html>"),
    )
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok and page.via == "browser"


@pytest.mark.asyncio
async def test_browser_failure_reports_reason_not_exception():
    fetcher, _, _, _ = make_fetcher(
        lambda url: FakeResponse(403, "<html>Just a moment...</html>"),
        lambda url: RuntimeError("playwright exploded"),
    )
    page = await fetcher.fetch("https://site.test/list")
    assert page.reason is FailureReason.BROWSER_UNAVAILABLE
    assert "playwright exploded" in page.message


@pytest.mark.asyncio
async def test_timeout_is_retried_then_reported():
    calls: list[str] = []

    def responder(url):
        calls.append(url)
        return asyncio.TimeoutError()

    fetcher, _, _, sleeps = make_fetcher(responder, browser_fallback=False)
    page = await fetcher.fetch("https://site.test/list")
    assert page.reason is FailureReason.TIMEOUT
    assert len(calls) == 2  # max_retries=1 -> 首次 + 1 次重试
    assert sleeps, "重试之间必须有退避"


@pytest.mark.asyncio
async def test_timeout_escalates_to_browser_when_allowed():
    fetcher, _, _, _ = make_fetcher(
        lambda url: asyncio.TimeoutError(),
        lambda url: FakeResponse(200, "<html>real content</html>"),
    )
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok and page.via == "browser"


@pytest.mark.asyncio
async def test_server_error_is_retried_then_succeeds():
    calls = {"n": 0}

    def responder(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(500, "")
        return FakeResponse(200, "<html>ok</html>")

    fetcher, _, _, _ = make_fetcher(responder)
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_not_found_is_not_retried():
    calls: list[str] = []

    def responder(url):
        calls.append(url)
        return FakeResponse(404, "")

    fetcher, _, _, _ = make_fetcher(responder)
    page = await fetcher.fetch("https://site.test/list")
    assert page.reason is FailureReason.HTTP_ERROR
    assert page.status == 404
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_http_session_is_reused_across_calls():
    FakeHttpCtx.instances = 0
    fetcher, _, _, _ = make_fetcher(lambda url: FakeResponse(200, "<html>ok</html>"))
    await fetcher.fetch("https://site.test/a")
    await fetcher.fetch("https://site.test/b")
    assert FakeHttpCtx.instances == 1
    await fetcher.close()


@pytest.mark.asyncio
async def test_browser_session_is_started_once_for_many_pages():
    FakeBrowser.started = 0
    fetcher, _, _, _ = make_fetcher(
        lambda url: FakeResponse(403, "<html>Just a moment...</html>"),
        lambda url: FakeResponse(200, "<html>ok</html>"),
    )
    await fetcher.fetch("https://site.test/a")
    await fetcher.fetch("https://site.test/b")
    assert FakeBrowser.started == 1


@pytest.mark.asyncio
async def test_browser_disabled_by_config_keeps_blocked_reason():
    fetcher, _, browsers, _ = make_fetcher(
        lambda url: FakeResponse(403, "<html>Just a moment...</html>"),
        browser_fallback=False,
    )
    page = await fetcher.fetch("https://site.test/list")
    assert page.reason is FailureReason.BLOCKED
    assert "browser" not in browsers


class RecordingAdmission:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def admit(self, url: str) -> str:
        self.urls.append(url)
        return url


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_rejected_before_the_hop():
    """公开页 302 到 127.0.0.1：第二跳必须先准入，且请求根本不发出。"""

    class BlockingAdmission(RecordingAdmission):
        async def admit(self, url: str) -> str:
            await super().admit(url)
            if "127.0.0.1" in url:
                raise ValueError("URL resolves to a private address")
            return url

    admission = BlockingAdmission()
    fetcher, holders, _, _ = make_fetcher(
        lambda url: FakeResponse(
            302,
            "",
            url=url,
            headers={"location": "http://127.0.0.1:9000/x"},
        ),
        browser_fallback=False,
    )
    fetcher._target_admission = admission
    page = await fetcher.fetch("https://site.test/list")
    assert page.reason is FailureReason.UNSAFE_URL
    assert "private" in page.message
    assert [call["url"] for call in holders["ctx"]._session.calls] == ["https://site.test/list"]
    assert admission.urls == ["http://127.0.0.1:9000/x"]


@pytest.mark.asyncio
async def test_relative_redirect_is_resolved_and_followed_without_client_redirects():
    def responder(url: str):
        if url == "https://site.test/list":
            return FakeResponse(302, "", url=url, headers={"Location": "/details/1"})
        return FakeResponse(200, "<html>ok</html>", url=url)

    fetcher, holders, _, _ = make_fetcher(responder)
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok
    calls = holders["ctx"]._session.calls
    assert [call["url"] for call in calls] == [
        "https://site.test/list",
        "https://site.test/details/1",
    ]
    assert all(call["follow_redirects"] is False for call in calls)


@pytest.mark.asyncio
async def test_cross_site_redirect_admits_the_new_host_and_requests_it():
    admission = RecordingAdmission()
    fetcher, holders, _, _ = make_fetcher(
        lambda url: (
            FakeResponse(
                302,
                "",
                url=url,
                headers={"location": "https://other.test/page"},
            )
            if url == "https://site.test/list"
            else FakeResponse(200, "<html>ok</html>", url=url)
        )
    )
    fetcher._target_admission = admission
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok
    assert [call["url"] for call in holders["ctx"]._session.calls] == [
        "https://site.test/list",
        "https://other.test/page",
    ]
    assert admission.urls == ["https://other.test/page"]


@pytest.mark.asyncio
async def test_https_to_http_redirect_is_allowed_when_admitted():
    admission = RecordingAdmission()
    fetcher, holders, _, _ = make_fetcher(
        lambda url: (
            FakeResponse(302, "", url=url, headers={"location": "http://site.test/page"})
            if url == "https://site.test/list"
            else FakeResponse(200, "<html>ok</html>", url=url)
        )
    )
    fetcher._target_admission = admission
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok
    assert [call["url"] for call in holders["ctx"]._session.calls] == [
        "https://site.test/list",
        "http://site.test/page",
    ]


@pytest.mark.asyncio
async def test_redirect_limit_stops_before_the_sixth_hop():
    seen: list[str] = []

    def responder(url: str):
        seen.append(url)
        return FakeResponse(302, "", url=url, headers={"location": f"/hop{len(seen)}"})

    fetcher, holders, _, _ = make_fetcher(responder)
    page = await fetcher.fetch("https://site.test/list")
    assert page.reason is FailureReason.HTTP_ERROR
    assert "上限: 5" in page.message
    assert len(seen) == 6
    assert [call["url"] for call in holders["ctx"]._session.calls][-1] == "https://site.test/hop5"


@pytest.mark.asyncio
async def test_redirect_without_location_is_an_http_error():
    fetcher, holders, _, _ = make_fetcher(
        lambda url: FakeResponse(302, "", url=url),
        browser_fallback=False,
    )
    page = await fetcher.fetch("https://site.test/list")
    assert page.reason is FailureReason.HTTP_ERROR
    assert page.message == "HTTP 302 缺少 Location"
    assert len(holders["ctx"]._session.calls) == 1


@pytest.mark.asyncio
async def test_redirect_chain_500_retries_the_whole_chain_from_the_original_url():
    rounds = {"n": 0}

    def responder(url: str):
        if url == "https://site.test/list":
            rounds["n"] += 1
            return FakeResponse(302, "", url=url, headers={"location": f"/round{rounds['n']}"})
        if url.endswith("/round1"):
            return FakeResponse(500, "<html>boom</html>", url=url)
        return FakeResponse(200, "<html>ok</html>", url=url)

    fetcher, holders, _, sleeps = make_fetcher(responder)
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok
    assert [call["url"] for call in holders["ctx"]._session.calls] == [
        "https://site.test/list",
        "https://site.test/round1",
        "https://site.test/list",
        "https://site.test/round2",
    ]
    assert len(sleeps) == 1


def test_empty_shell_detection():
    assert _looks_like_empty_shell("")
    assert _looks_like_empty_shell("<html><body><script src='app.js'></script></body></html>")
    assert not _looks_like_empty_shell("<html><body><a href='/details/1'>x</a></body></html>")


@pytest.mark.asyncio
async def test_host_rate_limiter_spaces_requests():
    limiter = HostRateLimiter(0.5)
    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    # 直接替换 asyncio.sleep，避免测试真的睡
    import magnet_harvester.page_fetch as module

    original = module.asyncio.sleep
    module.asyncio.sleep = fake_sleep
    try:
        await limiter.wait("site.test")
        await limiter.wait("site.test")
        await limiter.wait("site.test")
    finally:
        module.asyncio.sleep = original
    assert waits, "同一站点的连续请求必须被拉开间隔"


class RecordingRoute:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.continued = False
        self.aborted = False

    async def continue_(self) -> None:
        self.continued = True

    async def abort(self) -> None:
        self.aborted = True


class RecordingPage:
    def __init__(self) -> None:
        self.handler = None

    async def route(self, pattern: str, handler) -> None:
        self.handler = handler


@pytest.mark.asyncio
async def test_browser_subrequests_admit_each_origin_once():
    resolved: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> list[str]:
        resolved.append((host, port))
        return ["93.184.216.34"]

    admission = CrawlTargetAdmission(resolver=resolver)
    fetcher, _, _, _ = make_fetcher(lambda url: FakeResponse(200, "<html>ok</html>"))
    fetcher._target_admission = admission
    page = RecordingPage()
    await fetcher._guard_page_setup(page)
    for target in (
        "https://site.test/a.js",
        "https://site.test/b.css",
        "https://site.test/c.png",
        "https://other.test/x.js",
    ):
        route = RecordingRoute(target)
        await page.handler(route)
        assert route.continued and not route.aborted

    assert resolved == [("site.test", 443), ("other.test", 443)]


@pytest.mark.asyncio
async def test_browser_exception_is_retried_once():
    calls: list[str] = []

    def responder(url: str):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("playwright exploded")
        return FakeResponse(200, "<html>rendered</html>")

    fetcher, _, _, sleeps = make_fetcher(
        lambda url: FakeResponse(200, "<html>http</html>"),
        responder,
    )
    page = await fetcher.fetch("https://site.test/list", browser_first=True)
    assert page.ok and page.via == "browser"
    assert len(calls) == 2
    assert sleeps, "浏览器重试前要有退避"


@pytest.mark.asyncio
async def test_browser_fetch_respects_host_rate_limiter():
    class RecordingLimiter:
        def __init__(self) -> None:
            self.waits: list[str] = []

        async def wait(self, host: str) -> float:
            self.waits.append(host)
            return 0.0

    fetcher, _, _, _ = make_fetcher(
        lambda url: FakeResponse(200, "<html>http</html>"),
        lambda url: FakeResponse(200, "<html>rendered</html>"),
    )
    limiter = RecordingLimiter()
    fetcher._limiter = limiter
    await fetcher.fetch("https://site.test/a", browser_first=True)
    await fetcher.fetch("https://site.test/b", browser_first=True)
    assert limiter.waits == ["site.test", "site.test"]


@pytest.mark.asyncio
async def test_cross_site_redirect_holds_the_target_host_slot():
    """A 跳到 B 时，B 的并发槽要真的被占住，不能只算首跳。"""
    started_b = asyncio.Event()
    release_b = asyncio.Event()

    async def responder(url: str):
        if url == "https://a.test/start":
            return FakeResponse(302, "", url=url, headers={"location": "https://b.test/final"})
        if url == "https://b.test/final":
            started_b.set()
            await release_b.wait()
            return FakeResponse(200, "<html>final</html>", url=url)
        return FakeResponse(200, "<html>other</html>", url=url)

    fetcher, _, _, _ = make_fetcher(responder, per_host_concurrency=1)
    first = asyncio.create_task(fetcher.fetch("https://a.test/start"))
    await asyncio.wait_for(started_b.wait(), timeout=1)

    second = asyncio.create_task(fetcher.fetch("https://b.test/other"))
    await asyncio.sleep(0.05)
    assert not second.done(), "B 站的槽被重定向请求占着，第二条应排队"

    release_b.set()
    pages = await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
    assert all(page.ok for page in pages)


class ScriptRecordingPage(RecordingPage):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.context = SimpleNamespace(add_init_script=self._record_script)

    async def _record_script(self, script: str) -> None:
        self.scripts.append(script)


@pytest.mark.asyncio
async def test_browser_page_setup_disables_service_worker_registration():
    fetcher, _, _, _ = make_fetcher(lambda url: FakeResponse(200, "<html>ok</html>"))
    fetcher._target_admission = SimpleNamespace()
    page = ScriptRecordingPage()

    await fetcher._guard_page_setup(page)

    assert page.handler is not None
    assert page.scripts and "serviceWorker" in page.scripts[0]
