"""
MagnetCrawler v3.0 — 使用 Scrapling 引擎

使用 Scrapling 的动态页面抓取能力：
- Scrapling 管理浏览器页面加载
- 本模块专注于安全遍历与磁力链接提取

保持与 v2 相同的公共接口：
- start() / stop()
- crawl(url, depth) → AsyncGenerator[dict, None]
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, AsyncGenerator, List, Optional, Protocol, Set
from urllib.parse import urldefrag, urljoin, urlparse

try:
    from scrapling.fetchers import AsyncDynamicSession
except ImportError:  # pragma: no cover - dependency is declared, tests may monkeypatch it
    AsyncDynamicSession = None  # type: ignore[assignment]

from magnet_harvester.config import CrawlerConfig, settings
from magnet_harvester.magnet_sources import (
    MagnetSourceExtractor,
    filter_resolution_items as _filter_resolution_items,
)
from magnet_harvester.services.site_auth import SiteAuth
from magnet_harvester.utils.url_validator import (
    CrawlTargetAdmission,
    URLValidationError,
)
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)

DETAIL_URL_RE = re.compile(
    r".*(/(details?|torrent|view|resource|movie|subject)/|[?&](id|tid|movie_id|detail)=).*",
    re.IGNORECASE,
)


class CrawlMetrics:
    def __init__(self):
        self.start_time = time.time()
        self.pages_crawled = 0
        self.magnets_found = 0
        self.errors = 0

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def as_dict(self) -> dict:
        return {
            "elapsed_sec": round(self.elapsed, 1),
            "pages_crawled": self.pages_crawled,
            "magnets_found": self.magnets_found,
            "errors": self.errors,
        }


class CrawlPhase(Protocol):
    """Protocol for crawl adapters — implemented by MagnetCrawler."""

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]: ...
    async def admit_url(self, url: str) -> str: ...


def filter_resolution_items(items: List[dict], allowed: tuple = ("2160p", "4k")) -> List[dict]:
    return _filter_resolution_items(items, allowed=allowed)


class BrowserCookieProvider(Protocol):
    def browser_cookies(self) -> list[dict]: ...


@dataclass
class ScraplingPageResult:
    url: str
    success: bool
    html: str = ""
    cleaned_html: str = ""
    markdown: str = ""
    error_message: str = ""


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)
                return


class MagnetCrawler:
    """爬虫入口 — 使用 Scrapling 引擎

    公共接口：
        start()                — 启动浏览器引擎
        stop()                 — 关闭浏览器引擎
        crawl(url, depth=1)    — 异步生成器，产出事件消息
    """

    def __init__(
        self,
        config: CrawlerConfig = None,
        target_admission: CrawlTargetAdmission | None = None,
        site_auth: BrowserCookieProvider | None = None,
    ):
        self._config = config if config is not None else settings.crawler
        self._crawler: Optional[Any] = None
        self._session_metrics: ContextVar[CrawlMetrics | None] = ContextVar(
            "crawl_session_metrics",
            default=None,
        )
        self._seen_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._target_admission = target_admission or CrawlTargetAdmission()
        self._site_auth = site_auth or SiteAuth.from_raw(settings.SITE_COOKIES)
        self._magnet_sources = MagnetSourceExtractor(
            allowed_resolutions=self._config.allowed_resolutions
        )

    async def admit_url(self, url: str) -> str:
        return await self._target_admission.admit(url)

    @property
    def max_depth(self) -> int:
        return self._config.max_depth

    def _clamp_depth(self, depth: int) -> int:
        return max(1, min(int(depth), self._config.max_depth))

    def _current_metrics(self) -> CrawlMetrics:
        # 若 session_metrics 为 None（如 finally 块中 ContextVar 已清理），返回默认值。
        metrics = self._session_metrics.get()
        if metrics is None:
            log.warning("crawl metrics unavailable outside Crawl session, using defaults")
            return CrawlMetrics()
        return metrics

    async def start(self):
        """启动 Scrapling 引擎"""
        if AsyncDynamicSession is None:
            raise RuntimeError("Scrapling fetchers are not installed. Install scrapling[fetchers].")
        site_cookies = self._site_auth.browser_cookies()

        session = AsyncDynamicSession(
            headless=self._config.headless,
            timeout=self._config.timeout * 1000,
            network_idle=self._config.wait_until == "networkidle",
            max_pages=self._worker_count,
            retries=max(1, self._config.max_retries + 1),
            cookies=site_cookies if site_cookies else None,
        )
        try:
            if hasattr(session, "__aenter__"):
                await session.__aenter__()
        except BaseException:
            # start() 失败时关闭已创建的 session，防止浏览器进程泄漏
            # BaseException 覆盖 CancelledError（它是 BaseException 而非 Exception 的子类）
            await self._close_session(session)
            raise
        self._crawler = session
        log.info("Scrapling 引擎已启动")

    async def stop(self):
        """关闭 Scrapling 引擎"""
        if self._crawler:
            try:
                await self._close_session(self._crawler)
            except Exception as e:
                log.warning(f"关闭 Scrapling 时出错: {e}")
            finally:
                self._crawler = None
        if self._target_admission is not None and hasattr(self._target_admission, "close"):
            try:
                await self._target_admission.close()
            except Exception as e:
                log.warning(f"关闭 CrawlTargetAdmission 时出错: {e}")
        log.info("Scrapling 引擎已关闭")

    async def _close_session(self, session) -> None:
        if hasattr(session, "__aexit__"):
            await session.__aexit__(None, None, None)
            return
        close = getattr(session, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]:
        """爬取 URL 并提取磁力链接

        产出事件:
            {"type": "found", "item": {...}}     — 发现磁力链接
            {"type": "progress", ...}             — 进度信息
            {"type": "error", ...}                — 错误信息
            {"type": "done", "total": N, ...}     — 爬取完成
        """
        url = await self.admit_url(url)
        await self._target_admission.admit_redirect_chain(url)
        if not self._crawler:
            async with self._start_lock:
                if not self._crawler:
                    await self.start()

        effective_depth = self._clamp_depth(depth)
        self._session_metrics.set(CrawlMetrics())
        seen: Set[str] = set()
        events: asyncio.Queue[dict | None] = asyncio.Queue()

        session_task = BGTaskManager.spawn(
            self._run_crawl_session(
                root_url=url,
                events=events,
                seen=seen,
                depth=effective_depth,
            ),
            name="crawl-session",
        )

        crawl_timeout = max(60, effective_depth * self._config.timeout * 2)
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(events.get(), timeout=crawl_timeout)
                except asyncio.TimeoutError:
                    log.error(
                        "爬取会话超时 url=%s depth=%d timeout=%ds",
                        url,
                        effective_depth,
                        crawl_timeout,
                    )
                    yield {"type": "error", "msg": f"爬取超时 ({crawl_timeout}s)", "url": url}
                    break
                if msg is None:
                    break
                yield msg
        except Exception as e:
            log.error(f"爬取过程异常: {e}")
            yield {"type": "error", "msg": str(e), "url": url}
        finally:
            await self._finish_session_task(session_task)
            self._session_metrics.set(None)

    async def _finish_session_task(self, session_task: asyncio.Task) -> None:
        if not session_task.done():
            session_task.cancel()
        await asyncio.gather(session_task, return_exceptions=True)

    async def _run_crawl_session(
        self,
        root_url: str,
        events: asyncio.Queue[dict | None],
        seen: Set[str],
        depth: int,
    ) -> None:
        # 保存 CrawlMetrics 引用, 防止 finally 块中 ContextVar 已被外层设为 None
        metrics: CrawlMetrics = self._current_metrics()
        try:
            await events.put(
                {"type": "progress", "msg": "正在爬取...", "url": root_url, "depth": depth}
            )
            async for result in self._fetch_deep_stream(root_url, depth):
                try:
                    await self._handle_crawl_result(
                        result=result,
                        source_url=getattr(result, "url", root_url) or root_url,
                        events=events,
                        seen=seen,
                    )
                except Exception as exc:
                    metrics.errors += 1
                    log.exception(
                        "处理页面结果失败: %s",
                        getattr(result, "url", root_url) or root_url,
                    )
                    await events.put(
                        {
                            "type": "error",
                            "msg": str(exc),
                            "url": getattr(result, "url", root_url) or root_url,
                        }
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("深爬会话异常: %s", exc)
            metrics.errors += 1
            await events.put({"type": "error", "msg": str(exc), "url": root_url})
        finally:
            await events.put(
                {
                    "type": "done",
                    "total": metrics.magnets_found,
                    "url": root_url,
                    "metrics": metrics.as_dict(),
                }
            )
            await events.put(None)

    @property
    def _worker_count(self) -> int:
        return max(1, min(self._config.concurrency, 8))

    async def _handle_crawl_result(
        self,
        result,
        source_url: str,
        events: asyncio.Queue[dict | None],
        seen: Set[str],
    ) -> None:
        metrics = self._current_metrics()
        metrics.pages_crawled += 1
        result_url = getattr(result, "url", source_url) or source_url
        if not getattr(result, "success", False):
            metrics.errors += 1
            msg = getattr(result, "error_message", "") or "页面加载失败"
            await events.put({"type": "error", "msg": msg, "url": result_url})
            return

        items = self._extract_page_items(result, source_url=result_url)
        new_count = 0
        for item in items:
            hash_key = item.get("hash")
            if not hash_key:
                log.warning("跳过缺少 hash 字段的 item: %s", item)
                continue
            async with self._seen_lock:
                if hash_key in seen:
                    continue
                seen.add(hash_key)
            new_count += 1
            metrics.magnets_found += 1
            await events.put({"type": "found", "item": item})

        await events.put(
            {
                "type": "progress",
                "msg": f"发现 {new_count} 个新磁力",
                "url": result_url,
                "metrics": metrics.as_dict(),
            }
        )

    async def _fetch_deep_stream(self, root_url: str, depth: int):
        session = self._crawler
        if session is None:
            raise RuntimeError("爬虫已停止")
        max_pages = max(1, self._config.max_detail_links + 1)
        seen_urls = {self._normalise_url(root_url)}
        pending: list[tuple[str, int]] = [(root_url, 1)]
        pages_seen = 0

        while pending and pages_seen < max_pages:
            batch = pending[: self._worker_count]
            del pending[: self._worker_count]
            results = await asyncio.gather(
                *(self._fetch_page(session, url) for url, _ in batch),
                return_exceptions=True,
            )

            for (source_url, current_depth), fetched in zip(batch, results):
                pages_seen += 1
                if isinstance(fetched, Exception):
                    result = ScraplingPageResult(
                        url=source_url,
                        success=False,
                        error_message=str(fetched),
                    )
                else:
                    result = fetched
                yield result

                if current_depth >= depth or pages_seen + len(pending) >= max_pages:
                    continue
                for link in await self._discover_detail_links(result, root_url):
                    normalised = self._normalise_url(link)
                    if normalised in seen_urls or len(seen_urls) >= max_pages:
                        continue
                    seen_urls.add(normalised)
                    pending.append((link, current_depth + 1))

    async def _fetch_page(self, session, url: str) -> ScraplingPageResult:
        try:
            response = await session.fetch(
                url,
                timeout=self._config.timeout * 1000,
                network_idle=self._config.wait_until == "networkidle",
                wait=int(self._config.delay_before_return_html * 1000),
                page_action=self._prepare_dynamic_page,
            )
        except Exception as exc:
            return ScraplingPageResult(url=url, success=False, error_message=str(exc))

        response_url = getattr(response, "url", url) or url
        status = int(getattr(response, "status", 200) or 200)
        html = self._response_html(response)
        text = self._response_text(response)
        return ScraplingPageResult(
            url=response_url,
            success=200 <= status < 400,
            html=html,
            cleaned_html=html,
            markdown=text,
            error_message="" if 200 <= status < 400 else f"HTTP {status}",
        )

    async def _prepare_dynamic_page(self, page) -> None:
        if self._config.remove_overlay_elements or self._config.remove_consent_popups:
            await page.evaluate(
                """
                ({ removeOverlays, removeConsent }) => {
                    const shouldRemove = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        const text = (el.innerText || "").toLowerCase();
                        const looksModal = el.matches("dialog,[aria-modal='true'],[role='dialog']");
                        const blocksPage =
                            ["fixed", "sticky"].includes(style.position) &&
                            rect.width * rect.height > window.innerWidth * window.innerHeight * 0.25 &&
                            Number(style.zIndex || 0) >= 10;
                        const looksConsent =
                            /cookie|consent|privacy|同意|隐私|接受|accept|agree/.test(text);
                        const floats = looksModal || blocksPage || ["fixed", "sticky"].includes(style.position);
                        return looksModal || (removeOverlays && blocksPage) || (removeConsent && floats && looksConsent);
                    };
                    document.querySelectorAll("dialog,[aria-modal='true'],[role='dialog'],body *")
                        .forEach((el) => {
                            if (shouldRemove(el)) el.remove();
                        });
                    document.documentElement.style.overflow = "auto";
                    document.body.style.overflow = "auto";
                }
                """,
                {
                    "removeOverlays": self._config.remove_overlay_elements,
                    "removeConsent": self._config.remove_consent_popups,
                },
            )

        if self._config.scan_full_page:
            for _ in range(max(0, self._config.max_scroll_steps)):
                await page.evaluate("() => window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(int(self._config.scroll_delay * 1000))

        if self._config.process_iframes or self._config.flatten_shadow_dom:
            await page.evaluate(
                """
                ({ processIframes, flattenShadowDom }) => {
                    const sink = document.createElement("section");
                    sink.setAttribute("data-magnet-harvester-extra", "");
                    sink.hidden = true;
                    const append = (html) => {
                        if (!html) return;
                        const block = document.createElement("div");
                        block.innerHTML = html;
                        sink.appendChild(block);
                    };
                    if (processIframes) {
                        document.querySelectorAll("iframe").forEach((frame) => {
                            try {
                                append(frame.contentDocument?.documentElement?.outerHTML);
                            } catch {}
                        });
                    }
                    if (flattenShadowDom) {
                        const walk = (node) => {
                            if (node.shadowRoot) {
                                append(node.shadowRoot.innerHTML);
                                stack.push(...node.shadowRoot.querySelectorAll("*"));
                            }
                        };
                        const stack = [...document.querySelectorAll("*")];
                        while (stack.length) {
                            walk(stack.pop());
                        }
                    }
                    document.body?.appendChild(sink);
                }
                """,
                {
                    "processIframes": self._config.process_iframes,
                    "flattenShadowDom": self._config.flatten_shadow_dom,
                },
            )

    async def _discover_detail_links(
        self,
        result: ScraplingPageResult,
        root_url: str,
    ) -> list[str]:
        if not result.success:
            return []

        parser = LinkExtractor()
        parser.feed(result.html or result.cleaned_html or result.markdown)

        links: list[str] = []
        for href in parser.links:
            candidate = self._normalise_url(urljoin(result.url, href))
            if not self._is_detail_url(candidate) or not self._is_same_site(candidate, root_url):
                continue
            try:
                admitted = await self._target_admission.admit_redirect_chain(candidate)
            except URLValidationError:
                log.warning("跳过不安全的详情页链接: %s", candidate)
                continue
            if not self._is_same_site(admitted, root_url):
                continue
            links.append(self._normalise_url(admitted))
        return links

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
    def _response_html(response) -> str:
        for attr in ("html_content", "text"):
            value = getattr(response, attr, "")
            if isinstance(value, str) and value:
                return value
        body = getattr(response, "body", b"")
        if isinstance(body, bytes):
            return body.decode(getattr(response, "encoding", "utf-8") or "utf-8", errors="replace")
        return str(body or "")

    @staticmethod
    def _response_text(response) -> str:
        value = getattr(response, "text", "")
        return value if isinstance(value, str) else ""

    def _extract_page_items(self, result, source_url: str) -> List[dict]:
        return self._magnet_sources.from_page_result(result, source_url=source_url)
