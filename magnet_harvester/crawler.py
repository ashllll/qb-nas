"""
MagnetCrawler v3.0 — 使用 crawl4ai 引擎

使用 crawl4ai (AsyncWebCrawler) 替代直接 Playwright 操作：
- crawl4ai 管理浏览器生命周期、反爬、页面渲染
- 本模块专注于磁力链接提取

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
from typing import AsyncGenerator, List, Optional, Protocol, Set, runtime_checkable

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
)
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.deep_crawling.filters import FilterChain, URLFilter, URLPatternFilter
from crawl4ai.deep_crawling.scorers import PathDepthScorer

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


@runtime_checkable
class CrawlPhase(Protocol):
    """Protocol for crawl adapters — implemented by MagnetCrawler."""

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]: ...
    async def admit_url(self, url: str) -> str: ...


class CrawlAdmissionFilter(URLFilter):
    """Apply project URL safety checks inside crawl4ai's deep crawl filter chain."""

    def __init__(self, target_admission: CrawlTargetAdmission):
        super().__init__()
        self._target_admission = target_admission

    async def apply(self, url: str) -> bool:
        try:
            await self._target_admission.admit_redirect_chain(url)
        except URLValidationError:
            self._update_stats(False)
            log.warning("跳过不安全的详情页链接: %s", url)
            return False
        self._update_stats(True)
        return True


def filter_resolution_items(items: List[dict], allowed: tuple = ("2160p", "4k")) -> List[dict]:
    return _filter_resolution_items(items, allowed=allowed)


class BrowserCookieProvider(Protocol):
    def browser_cookies(self) -> list[dict]: ...


class MagnetCrawler:
    """爬虫入口 — 使用 crawl4ai 引擎

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
        self._crawler: Optional[AsyncWebCrawler] = None
        self._metrics: Optional[CrawlMetrics] = None
        self._session_metrics: ContextVar[CrawlMetrics | None] = ContextVar(
            "crawl_session_metrics",
            default=None,
        )
        self._seen_lock = asyncio.Lock()
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
        """启动 crawl4ai 引擎"""
        site_cookies = self._site_auth.browser_cookies()

        browser_cfg = BrowserConfig(
            browser_type="chromium",
            headless=self._config.headless,
            verbose=False,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            text_mode=True,
            cookies=site_cookies if site_cookies else None,
        )
        crawler = AsyncWebCrawler(config=browser_cfg)
        try:
            await crawler.start()
        except Exception:
            # start() 失败时关闭已创建的 crawler，防止浏览器进程泄漏
            await crawler.close()
            raise
        self._crawler = crawler
        log.info("crawl4ai 引擎已启动")

    async def stop(self):
        """关闭 crawl4ai 引擎"""
        if self._crawler:
            try:
                await self._crawler.close()
            except Exception as e:
                log.warning(f"关闭 crawl4ai 时出错: {e}")
            finally:
                self._crawler = None
        if self._target_admission is not None and hasattr(self._target_admission, "close"):
            try:
                await self._target_admission.close()
            except Exception as e:
                log.warning(f"关闭 CrawlTargetAdmission 时出错: {e}")
        log.info("crawl4ai 引擎已关闭")

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

        try:
            while True:
                msg = await events.get()
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
        result_stream = await self._crawler.arun(
            root_url,
            config=self._build_run_config(
                stream=True,
                deep_crawl_strategy=self._build_deep_crawl_strategy(depth),
            ),
        )
        async for result in result_stream:
            yield result

    def _build_deep_crawl_strategy(self, depth: int) -> BFSDeepCrawlStrategy:
        url_scorer = PathDepthScorer() if self._config.url_score_depth_bias else None
        return BFSDeepCrawlStrategy(
            max_depth=max(0, depth - 1),
            filter_chain=FilterChain(
                [
                    URLPatternFilter(DETAIL_URL_RE, use_glob=False),
                    CrawlAdmissionFilter(self._target_admission),
                ]
            ),
            url_scorer=url_scorer,
            include_external=False,
            max_pages=max(1, self._config.max_detail_links + 1),
            logger=log,
        )

    def _build_run_config(
        self,
        stream: bool = False,
        deep_crawl_strategy: BFSDeepCrawlStrategy | None = None,
    ) -> CrawlerRunConfig:
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=self._config.word_count_threshold,
            verbose=False,
            stream=stream,
            page_timeout=self._config.timeout * 1000,
            wait_until=self._config.wait_until,
            delay_before_return_html=self._config.delay_before_return_html,
            scan_full_page=self._config.scan_full_page,
            scroll_delay=self._config.scroll_delay,
            max_scroll_steps=self._config.max_scroll_steps,
            process_iframes=self._config.process_iframes,
            flatten_shadow_dom=self._config.flatten_shadow_dom,
            remove_overlay_elements=self._config.remove_overlay_elements,
            remove_consent_popups=self._config.remove_consent_popups,
            deep_crawl_strategy=deep_crawl_strategy,
            semaphore_count=self._worker_count,
            max_retries=self._config.max_retries,
            check_robots_txt=self._config.check_robots_txt,
            simulate_user=self._config.simulate_user,
            magic=self._config.magics,
        )

    def _extract_page_items(self, result, source_url: str) -> List[dict]:
        return self._magnet_sources.from_page_result(result, source_url=source_url)
