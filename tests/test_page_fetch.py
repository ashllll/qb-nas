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

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.page_fetch import (
    FailureReason,
    HostRateLimiter,
    PageFetcher,
    _looks_like_empty_shell,
)


class FakeResponse:
    def __init__(self, status: int, html: str, url: str = "https://site.test/x") -> None:
        self.status = status
        self.html_content = html
        self.text = html
        self.url = url
        self.headers: dict[str, str] = {}


class FakeHttpSession:
    def __init__(self, responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, float | None]] = []

    async def get(self, url: str, timeout: float | None = None):
        self.calls.append((url, timeout))
        result = self.responder(url)
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
    assert fetcher.browser_used is False
    assert "browser" not in browsers


@pytest.mark.asyncio
async def test_challenge_page_escalates_to_browser():
    fetcher, _, browsers, _ = make_fetcher(
        lambda url: FakeResponse(403, "<html>Just a moment...</html>"),
        lambda url: FakeResponse(200, "<html><a href='/details/1'>1</a></html>"),
    )
    page = await fetcher.fetch("https://site.test/list")
    assert page.ok
    assert page.via == "browser"
    assert fetcher.browser_used is True
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
    assert fetcher.browser_failures == 1


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


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_rejected():
    """跟随重定向后必须重新准入：公开页 302 到 127.0.0.1 不能放行。"""

    class BlockingAdmission:
        async def admit(self, url: str) -> str:
            if "127.0.0.1" in url:
                raise ValueError("URL resolves to a private address")
            return url

    fetcher, _, _, _ = make_fetcher(
        lambda url: FakeResponse(200, "<html>moved</html>", url="http://127.0.0.1:9000/x"),
        browser_fallback=False,
    )
    fetcher._target_admission = BlockingAdmission()
    page = await fetcher.fetch("https://site.test/list")
    assert page.reason is FailureReason.UNSAFE_URL
    assert "private" in page.message


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
