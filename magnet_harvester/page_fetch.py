"""取页层：一次请求的可靠性全部收在这里。

设计顺序（按对用户的重要性）：

1. **快**：公开页先用 ``FetcherSession(impersonate="chrome")`` 拿 HTML。
   只有确认需要 JS（Cloudflare/空壳页/403）才升级浏览器；浏览器会话跨抓取复用。
2. **稳**：按站点限速 + 并发上限 + 单请求超时 + 指数退避；失败落到枚举原因，
   下游（中心服务）能把「为什么没结果」原样说给用户。
3. **省**：HTTP 会话与浏览器会话都由 ``PageFetcher`` 长期持有，
   一次 ``crawl`` 结束后不销毁，下一次复制网址直接复用连接与浏览器。

这一层不知道「磁力」是什么，只负责把 URL 变成 HTML（或一个明确的原因）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from scrapling.fetchers import AsyncDynamicSession, FetcherSession

from magnet_harvester.config import CrawlerConfig

log = logging.getLogger(__name__)


class FailureReason(str, Enum):
    """失败原因枚举 — 与《约定》一致，p1 直接透传给 Web UI。"""

    NO_MAGNET = "no_magnet"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    PARSE_ERROR = "parse_error"
    UNSAFE_URL = "unsafe_url"
    BROWSER_UNAVAILABLE = "browser_unavailable"


#: 命中任一标记就认为「HTTP 拿到的是人机校验页，不是内容」。
CHALLENGE_MARKERS = (
    "just a moment",
    "cf-challenge",
    "challenge-platform",
    "attention required",
    "ddos protection",
    "enable javascript and cookies",
    "verify you are human",
    "checking your browser",
    "cf_chl_opt",
)


@dataclass
class PageFetch:
    """一次取页的结果。``reason`` 为空才算成功。"""

    url: str
    status: int = 0
    html: str = ""
    via: str = "http"
    elapsed_ms: int = 0
    reason: FailureReason | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.reason is None

    def as_event(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "html": self.html,
            "via": self.via,
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason.value if self.reason else None,
            "message": self.message,
            "success": self.ok,
        }


class HostRateLimiter:
    """每个站点一条最小间隔通道：并发再高，发出去的请求也不挤在一起。"""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = max(0.0, float(min_interval))
        self._next_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, host: str) -> float:
        if self._min_interval <= 0:
            return 0.0
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            delay = max(0.0, self._next_at.get(host, 0.0) - now)
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()
            self._next_at[host] = now + self._min_interval
            return delay


def _looks_like_challenge(html: str) -> bool:
    lowered = (html or "")[:20000].lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def _looks_like_empty_shell(html: str) -> bool:
    """页面 HTTP 200，但正文几乎是空的、只有脚本 —— 典型 SPA/JS 门。"""
    if not html:
        return True
    lowered = html.lower()
    if "<a " in lowered:
        return False
    text_len = len(lowered.split("<body", 1)[-1])
    return text_len < 400 and "<script" in lowered


class PageFetcher:
    """HTTP 优先、按需升级浏览器的取页器（会话长期复用）。

    依赖注入点：``http_factory`` / ``browser_factory`` 让测试完全离线。
    """

    def __init__(
        self,
        config: CrawlerConfig,
        *,
        http_factory: Callable[[], Any] | None = None,
        browser_factory: Callable[[], Any] | None = None,
        target_admission: Any | None = None,
        page_preparer: Any | None = None,
        cookies: list[dict] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._http_factory = http_factory or self._default_http_factory
        self._browser_factory = browser_factory or self._default_browser_factory
        self._target_admission = target_admission
        self._page_preparer = page_preparer
        self._cookies = cookies or []
        self._sleep = sleep or asyncio.sleep

        self._http_ctx: Any = None
        self._http: Any = None
        self._browser: Any = None
        self._http_lock = asyncio.Lock()
        self._browser_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._limiter = HostRateLimiter(config.per_host_min_interval)
        self._host_slots: dict[str, asyncio.Semaphore] = {}

        self.browser_used = False
        self.browser_failures = 0
        self.requests_sent = 0

    # ── 会话生命周期 ─────────────────────────

    def _default_http_factory(self) -> Any:
        return FetcherSession(
            impersonate=self._config.http_impersonate,
            timeout=self._config.timeout,
            # Scrapling 的 retries 是 range() 次数，0 会直接抛 "No active session"；
            # 这里固定 1（= 发一次），重试策略由本模块自己控制。
            retries=1,
            retry_delay=0,
            follow_redirects=True,
        )

    def _default_browser_factory(self) -> Any:
        return AsyncDynamicSession(
            headless=self._config.headless,
            timeout=self._config.timeout * 1000,
            network_idle=self._config.wait_until == "networkidle",
            max_pages=max(1, self._config.browser_max_pages),
            retries=1,
            disable_resources=self._config.disable_resources,
            cookies=self._cookies or None,
        )

    async def start(self) -> None:
        await self._ensure_http()

    async def close(self) -> None:
        await self._close_http()
        await self._close_browser()

    async def _close_http(self) -> None:
        ctx, self._http_ctx, self._http = self._http_ctx, None, None
        if ctx is None:
            return
        try:
            await ctx.__aexit__(None, None, None)
        except Exception as exc:  # pragma: no cover - 关闭失败不值得中断调用方
            log.debug("关闭 HTTP 会话时出错: %s", exc)

    async def _close_browser(self) -> None:
        browser, self._browser = self._browser, None
        if browser is None:
            return
        try:
            await browser.close()
        except Exception as exc:  # pragma: no cover
            log.debug("关闭浏览器会话时出错: %s", exc)

    def _loop_changed(self) -> bool:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover
            return False
        changed = self._loop is not None and self._loop is not loop
        self._loop = loop
        return changed

    async def _ensure_http(self) -> Any:
        if self._loop_changed():
            # 事件循环换了（测试/重启），旧会话作废，避免跨循环使用。
            await self._close_http()
            await self._close_browser()
        async with self._http_lock:
            if self._http is not None:
                return self._http
            ctx = self._http_factory()
            self._http_ctx = ctx
            self._http = await ctx.__aenter__()
            return self._http

    async def _ensure_browser(self) -> Any:
        async with self._browser_lock:
            if self._browser is not None:
                return self._browser
            browser = self._browser_factory()
            await browser.start()
            self._browser = browser
            return browser

    # ── 对外唯一入口 ─────────────────────────

    async def fetch(self, url: str, *, allow_browser: bool = True) -> PageFetch:
        """取一页：先 HTTP，必要时升级浏览器。永不抛异常，失败落在 ``reason``。"""
        host = (urlparse(url).hostname or "").lower()
        async with self._slot(host):
            page = await self._http_fetch(url)
            if not allow_browser or not self._should_escalate(page):
                return page
            log.info("HTTP 未拿到内容，升级浏览器: %s (%s)", url, page.reason or "js_shell")
            browser_page = await self._browser_fetch(url)
            if browser_page.ok or not page.ok:
                return browser_page
            return page

    def _slot(self, host: str) -> asyncio.Semaphore:
        slot = self._host_slots.get(host)
        if slot is None:
            slot = asyncio.Semaphore(max(1, self._config.per_host_concurrency))
            self._host_slots[host] = slot
        return slot

    def _should_escalate(self, page: PageFetch) -> bool:
        if not self._config.browser_fallback:
            return False
        if not self._config.http_first:
            return True
        if page.reason is FailureReason.BLOCKED:
            return True
        if page.reason is FailureReason.TIMEOUT:
            return True
        if page.ok:
            return _looks_like_empty_shell(page.html)
        if page.reason is FailureReason.HTTP_ERROR and page.status in (403, 429, 503):
            return True
        return False

    # ── HTTP 路径 ────────────────────────────

    async def _http_fetch(self, url: str) -> PageFetch:
        host = (urlparse(url).hostname or "").lower()
        attempts = max(0, int(self._config.max_retries)) + 1
        last: PageFetch | None = None
        for attempt in range(attempts):
            await self._limiter.wait(host)
            started = time.monotonic()
            try:
                session = await self._ensure_http()
                self.requests_sent += 1
                response = await session.get(url, timeout=self._config.timeout)
            except Exception as exc:
                last = self._failure_from_exception(url, exc, started)
            else:
                last = self._page_from_response(url, response, started)
                if last.url and last.url != url and self._target_admission is not None:
                    try:
                        await self._target_admission.admit(last.url)
                    except Exception as exc:
                        last = PageFetch(
                            url=last.url,
                            status=last.status,
                            via="http",
                            elapsed_ms=last.elapsed_ms,
                            reason=FailureReason.UNSAFE_URL,
                            message=f"重定向到不安全地址: {exc}"[:300],
                        )
            if last.ok:
                return last
            if not self._retryable(last) or attempt + 1 >= attempts:
                return last
            backoff = self._backoff_delay(attempt)
            log.info("HTTP 重试 %s (%s)，%.2fs 后再试", url, last.reason.value, backoff)
            await self._sleep(backoff)
        assert last is not None
        return last

    @staticmethod
    def _retryable(page: PageFetch) -> bool:
        if page.reason is FailureReason.TIMEOUT:
            return True
        if page.reason is FailureReason.HTTP_ERROR and page.status in (429, 500, 502, 503, 504):
            return True
        return False

    def _backoff_delay(self, attempt: int) -> float:
        base = max(0.1, float(self._config.retry_backoff))
        return min(8.0, base * (2**attempt)) + random.uniform(0, base / 2)

    def _page_from_response(self, url: str, response: Any, started: float) -> PageFetch:
        status = int(getattr(response, "status", 0) or 0)
        html = _response_html(response)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        final_url = str(getattr(response, "url", url) or url)
        if 200 <= status < 400:
            if _looks_like_challenge(html):
                return PageFetch(
                    url=final_url,
                    status=status,
                    html=html,
                    via="http",
                    elapsed_ms=elapsed_ms,
                    reason=FailureReason.BLOCKED,
                    message=f"HTTP {status} 人机校验页",
                )
            return PageFetch(url=final_url, status=status, html=html, elapsed_ms=elapsed_ms)
        reason = FailureReason.BLOCKED if status in (403, 429, 503) else FailureReason.HTTP_ERROR
        return PageFetch(
            url=final_url,
            status=status,
            html=html,
            elapsed_ms=elapsed_ms,
            reason=reason,
            message=f"HTTP {status}",
        )

    def _failure_from_exception(self, url: str, exc: Exception, started: float) -> PageFetch:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        name = type(exc).__name__.lower()
        text = str(exc) or name
        if (
            isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            or "timeout" in name
            or "timed out" in text.lower()
        ):
            return PageFetch(
                url=url,
                elapsed_ms=elapsed_ms,
                reason=FailureReason.TIMEOUT,
                message=f"请求超时（{self._config.timeout}s）",
            )
        return PageFetch(
            url=url,
            elapsed_ms=elapsed_ms,
            reason=FailureReason.HTTP_ERROR,
            message=f"{name}: {text}"[:300],
        )

    # ── 浏览器路径 ───────────────────────────

    async def _browser_fetch(self, url: str) -> PageFetch:
        started = time.monotonic()
        try:
            browser = await self._ensure_browser()
            self.browser_used = True
            self.requests_sent += 1
            kwargs: dict[str, Any] = {
                "timeout": self._config.timeout * 1000,
                "network_idle": self._config.wait_until == "networkidle",
                "wait": int(self._config.delay_before_return_html * 1000),
                "disable_resources": self._config.disable_resources,
            }
            if self._page_preparer is not None:
                kwargs["page_action"] = self._page_preparer.prepare
            if self._target_admission is not None:
                kwargs["page_setup"] = self._guard_page_setup
            response = await browser.fetch(url, **kwargs)
        except Exception as exc:
            self.browser_failures += 1
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.warning("浏览器取页失败 %s: %s", url, exc)
            return PageFetch(
                url=url,
                via="browser",
                elapsed_ms=elapsed_ms,
                reason=FailureReason.BROWSER_UNAVAILABLE,
                message=f"{type(exc).__name__}: {exc}"[:300],
            )
        page = self._page_from_response(url, response, started)
        page.via = "browser"
        if page.reason is FailureReason.BLOCKED:
            page.message = page.message or "浏览器拿到人机校验页"
        return page

    async def _guard_page_setup(self, page: Any) -> None:
        """浏览器里发出的每个子请求都先过 SSRF 准入（与旧行为一致）。"""
        admission = self._target_admission

        async def _route_handler(route: Any) -> None:
            target = str(route.request.url)
            if urlparse(target).scheme in {"data", "blob"}:
                await route.continue_()
                return
            try:
                await admission.admit(target)
            except Exception as exc:
                log.warning("阻止不安全的浏览器请求 %s: %s", target, exc)
                await route.abort()
                return
            await route.continue_()

        await page.route("**/*", _route_handler)


def _response_html(response: Any) -> str:
    for attr in ("html_content", "text"):
        value = getattr(response, attr, "")
        if isinstance(value, str) and value:
            return value
    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        encoding = getattr(response, "encoding", "utf-8") or "utf-8"
        return body.decode(encoding, errors="replace")
    return str(body or "")
