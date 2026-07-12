"""Scrapling Spider implementation for magnet page discovery."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
from uuid import uuid4

from scrapling.fetchers import AsyncDynamicSession, FetcherSession
from scrapling.spiders import Request, Response, Spider
from scrapling.spiders.session import SessionManager

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.dynamic_page import DynamicPagePolicy
from magnet_harvester.utils.url_validator import CrawlTargetAdmission, URLValidationError

log = logging.getLogger(__name__)
ErrorSink = Callable[[str, str], Awaitable[None]]

DETAIL_URL_RE = re.compile(
    r".*(/(details?|torrent|view|resource|movie|subject)/|[?&](id|tid|movie_id|detail)=).*",
    re.IGNORECASE,
)


class MagnetSpider(Spider):
    """Crawl one site entirely through Scrapling's scheduler and sessions."""

    name = "magnet-harvester"
    logging_level = logging.WARNING

    def __init__(
        self,
        *,
        root_url: str,
        depth: int,
        config: CrawlerConfig,
        target_admission: CrawlTargetAdmission,
        cookies: list[dict[str, Any]],
    ) -> None:
        self.root_url = self._normalise_url(root_url)
        self.name = f"magnet-harvester-{uuid4().hex}"
        self.start_urls = [self.root_url]
        # Scrapling compares allowed_domains with netloc (including ports). Our parse callback
        # performs a stricter hostname admission before every follow request.
        self.allowed_domains = set()
        self.depth = max(1, min(int(depth), config.max_depth))
        self.concurrent_requests = max(1, min(config.concurrency, 8))
        self.concurrent_requests_per_domain = self.concurrent_requests
        self.download_delay = 0.0
        self.max_blocked_retries = max(0, config.max_retries)
        self.robots_txt_obey = config.check_robots_txt

        self._config = config
        self._target_admission = target_admission
        self._cookies = cookies
        self._dynamic_page = DynamicPagePolicy(config)
        self._detail_limit = max(0, config.max_detail_links)
        self._scheduled_details: set[str] = set()
        self._pending_details: set[str] = set()
        self._schedule_lock = asyncio.Lock()
        self._admission_limit = asyncio.Semaphore(self.concurrent_requests)
        self._error_sink: ErrorSink | None = None
        self._blocked_counts: dict[str, int] = {}
        self.errors: list[dict[str, str]] = []
        super().__init__()

    def configure_sessions(self, manager: SessionManager) -> None:
        if self.robots_txt_obey:
            manager.add(
                "robots",
                FetcherSession(follow_redirects=False),
                default=True,
                lazy=True,
            )
        manager.add(
            "browser",
            AsyncDynamicSession(
                headless=self._config.headless,
                timeout=self._config.timeout * 1000,
                network_idle=self._config.wait_until == "networkidle",
                max_pages=self.concurrent_requests,
                retries=max(1, self._config.max_retries + 1),
                cookies=self._cookies or None,
                additional_args={"service_workers": "block"},
            ),
            default=not self.robots_txt_obey,
        )

    def _request(self, url: str, *, depth: int) -> Request:
        return Request(
            url,
            sid="browser",
            callback=self.parse,
            meta={"depth": depth},
            timeout=self._config.timeout * 1000,
            network_idle=self._config.wait_until == "networkidle",
            wait=int(self._config.delay_before_return_html * 1000),
            page_action=self._dynamic_page.prepare,
            page_setup=self._setup_page,
        )

    async def start_requests(self) -> AsyncGenerator[Request, None]:
        yield self._request(self.root_url, depth=1)

    async def stream(self) -> AsyncGenerator[dict[str, Any], None]:
        try:
            async for item in super().stream():
                yield item
        finally:
            self._release_logger()

    async def parse(
        self,
        response: Response,
    ) -> AsyncGenerator[dict[str, Any] | Request | None, None]:
        response_url = self._normalise_url(str(response.url))
        try:
            admitted_url = await self._target_admission.admit(response_url)
        except URLValidationError as exc:
            await self._emit_error(response_url, str(exc))
            return
        if not self._is_same_site(admitted_url, self.root_url):
            await self._emit_error(response_url, "最终响应已离开允许站点")
            return

        status = int(getattr(response, "status", 200) or 200)
        html = self._response_html(response)
        yield {
            "kind": "page",
            "url": admitted_url,
            "success": 200 <= status < 400,
            "html": html,
            "cleaned_html": html,
            "markdown": self._response_text(response),
            "error_message": "" if 200 <= status < 400 else f"HTTP {status}",
        }

        current_depth = int(getattr(response, "meta", {}).get("depth", 1))
        if status < 200 or status >= 400 or current_depth >= self.depth:
            return

        candidates: list[str] = []
        seen_candidates: set[str] = set()
        for href in response.css("a::attr(href)").getall():
            candidate = self._normalise_url(urljoin(admitted_url, str(href)))
            if not self._is_detail_url(candidate) or not self._is_same_site(
                candidate, self.root_url
            ):
                continue
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            candidates.append(candidate)

        while candidates:
            claimed = await self._claim_detail_candidates(candidates)
            if not claimed:
                break
            claimed_set = set(claimed)
            candidates = [candidate for candidate in candidates if candidate not in claimed_set]
            admitted_candidates = await asyncio.gather(
                *(self._admit_detail(candidate) for candidate in claimed)
            )
            for candidate, admitted in zip(claimed, admitted_candidates):
                if not await self._finish_detail_candidate(candidate, admitted):
                    continue
                assert admitted is not None
                if not self._is_same_site(admitted, self.root_url):
                    continue
                yield response.follow(
                    admitted,
                    sid="browser",
                    callback=self.parse,
                    meta={"depth": current_depth + 1},
                    timeout=self._config.timeout * 1000,
                    network_idle=self._config.wait_until == "networkidle",
                    wait=int(self._config.delay_before_return_html * 1000),
                    page_action=self._dynamic_page.prepare,
                    page_setup=self._setup_page,
                )

    async def is_blocked(self, response: Response) -> bool:
        blocked = await super().is_blocked(response)
        url = str(response.url)
        if blocked:
            attempts = self._blocked_counts.get(url, 0) + 1
            self._blocked_counts[url] = attempts
        else:
            self._blocked_counts.pop(url, None)
        if blocked and attempts > self.max_blocked_retries:
            await self._emit_error(str(response.url), f"HTTP {response.status}，阻断重试已耗尽")
        return blocked

    async def on_error(self, request: Request, error: Exception) -> None:
        await self._emit_error(request.url, str(error))

    async def on_close(self) -> None:
        await super().on_close()
        self._release_logger()

    def _release_logger(self) -> None:
        self.logger.handlers.clear()
        manager = logging.Logger.manager
        manager.loggerDict.pop(self.logger.name, None)
        for name, entry in tuple(manager.loggerDict.items()):
            logger_map = getattr(entry, "loggerMap", None)
            if logger_map is None:
                continue
            logger_map.pop(self.logger, None)
            if not logger_map:
                manager.loggerDict.pop(name, None)

    def set_error_sink(self, sink: ErrorSink) -> None:
        self._error_sink = sink

    def request_stop(self) -> None:
        try:
            self.pause()
        except RuntimeError:
            return

    async def _emit_error(self, url: str, message: str) -> None:
        if self._error_sink is not None:
            await self._error_sink(url, message)
            return
        self.errors.append({"url": url, "message": message})

    async def _setup_page(self, page) -> None:
        await page.route("**/*", self._guard_browser_request)
        await page.route_web_socket("**/*", self._guard_websocket_request)

    async def _guard_browser_request(self, route) -> None:
        url = str(route.request.url)
        if urlparse(url).scheme in {"data", "blob"}:
            await route.continue_()
            return
        try:
            await self._target_admission.admit(url)
        except Exception as exc:
            log.warning("阻止不安全的浏览器请求 %s: %s", url, exc)
            await route.abort()
            return
        await route.continue_()

    async def _guard_websocket_request(self, route) -> None:
        parsed = urlparse(str(route.url))
        probe_scheme = "https" if parsed.scheme == "wss" else "http"
        probe_url = urlunparse(parsed._replace(scheme=probe_scheme))
        try:
            await self._target_admission.admit(probe_url)
        except Exception as exc:
            log.warning("阻止不安全的 WebSocket 请求 %s: %s", route.url, exc)
            await route.close()
            return
        route.connect_to_server()

    async def _admit_detail(self, candidate: str) -> str | None:
        async with self._admission_limit:
            try:
                return await self._target_admission.admit(candidate)
            except Exception as exc:
                log.warning("跳过不安全的详情页链接 %s: %s", candidate, exc)
                return None

    async def _claim_detail_candidates(self, candidates: list[str]) -> list[str]:
        async with self._schedule_lock:
            available = (
                self._detail_limit - len(self._scheduled_details) - len(self._pending_details)
            )
            claimed: list[str] = []
            for candidate in candidates:
                if available <= 0:
                    break
                if candidate in self._scheduled_details or candidate in self._pending_details:
                    continue
                self._pending_details.add(candidate)
                claimed.append(candidate)
                available -= 1
            return claimed

    async def _finish_detail_candidate(self, candidate: str, admitted: str | None) -> bool:
        async with self._schedule_lock:
            self._pending_details.discard(candidate)
            if admitted is None:
                return False
            normalised = self._normalise_url(admitted)
            if normalised in self._scheduled_details:
                return False
            self._scheduled_details.add(normalised)
            return True

    @staticmethod
    def _normalise_url(url: str) -> str:
        return urldefrag(url)[0]

    @staticmethod
    def _is_same_site(url: str, root_url: str) -> bool:
        return (urlparse(url).hostname or "").lower() == (urlparse(root_url).hostname or "").lower()

    @staticmethod
    def _is_detail_url(url: str) -> bool:
        return bool(DETAIL_URL_RE.match(url))

    @staticmethod
    def _response_html(response: Response) -> str:
        for attr in ("html_content", "text"):
            value = getattr(response, attr, "")
            if isinstance(value, str) and value:
                return value
        body = getattr(response, "body", b"")
        if isinstance(body, bytes):
            encoding = getattr(response, "encoding", "utf-8") or "utf-8"
            return body.decode(encoding, errors="replace")
        return str(body or "")

    @staticmethod
    def _response_text(response: Response) -> str:
        value = getattr(response, "text", "")
        return value if isinstance(value, str) else ""
