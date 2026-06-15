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
import random
import time
from contextvars import ContextVar
from urllib.parse import urljoin, urlparse
from typing import AsyncGenerator, List, Optional, Set

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    MemoryAdaptiveDispatcher,
    RateLimiter,
)

from magnet_harvester.config import CrawlerConfig, settings
from magnet_harvester.magnet_parser import extract_from_text
from magnet_harvester.services.site_auth import parse_site_cookies
from magnet_harvester.utils.url_validator import (
    CrawlTargetAdmission,
    URLValidationError,
)

log = logging.getLogger(__name__)


class CrawlMetrics:
    def __init__(self):
        self.start_time = time.time()
        self.pages_crawled = 0
        self.magnets_found = 0
        self.errors = 0
        self.retries = 0

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def as_dict(self) -> dict:
        return {
            "elapsed_sec": round(self.elapsed, 1),
            "pages_crawled": self.pages_crawled,
            "magnets_found": self.magnets_found,
            "errors": self.errors,
            "retries": self.retries,
        }




def filter_resolution_items(items: List[dict], allowed: tuple = ("2160p", "4k")) -> List[dict]:
    """Filter crawler results to the configured resolution keywords."""
    allowed_lower = {a.lower() for a in allowed if a}
    return [it for it in items if any(ar in it.get("name", "").lower() for ar in allowed_lower)]


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
    ):
        if config is None:
            self._config = CrawlerConfig(
                timeout=settings.CRAWLER_TIMEOUT,
                max_depth=settings.CRAWLER_MAX_DEPTH,
                concurrency=settings.CRAWLER_CONCURRENCY,
                max_detail_links=settings.CRAWLER_MAX_DETAIL_LINKS,
                headless=settings.CRAWLER_HEADLESS,
                allowed_resolutions=settings.crawler.allowed_resolutions,
                wait_until=settings.CRAWLER_WAIT_UNTIL,
                delay_before_return_html=settings.CRAWLER_DELAY_BEFORE_HTML,
                scan_full_page=settings.CRAWLER_SCAN_FULL_PAGE,
                scroll_delay=settings.CRAWLER_SCROLL_DELAY,
                max_scroll_steps=settings.CRAWLER_MAX_SCROLL_STEPS,
                process_iframes=settings.CRAWLER_PROCESS_IFRAMES,
                flatten_shadow_dom=settings.CRAWLER_FLATTEN_SHADOW_DOM,
                remove_overlay_elements=settings.CRAWLER_REMOVE_OVERLAYS,
                remove_consent_popups=settings.CRAWLER_REMOVE_CONSENT_POPUPS,
            )
        else:
            self._config = config
        self._crawler: Optional[AsyncWebCrawler] = None
        self._metrics: Optional[CrawlMetrics] = None
        self._session_metrics: ContextVar[CrawlMetrics | None] = ContextVar(
            "crawl_session_metrics",
            default=None,
        )
        self._visited_lock = asyncio.Lock()
        self._seen_lock = asyncio.Lock()
        self._target_admission = target_admission or CrawlTargetAdmission()

    async def admit_url(self, url: str) -> str:
        return await self._target_admission.admit(url)

    @staticmethod
    def _build_site_cookies() -> list[dict]:
        """从配置构建全局站点 cookie 列表（Playwright 格式）。"""
        site_cookies = parse_site_cookies(settings.SITE_COOKIES)
        all_cookies: list[dict] = []
        for domain, cookie_str in site_cookies.items():
            if not domain or not cookie_str:
                continue
            # 解析 cookie 字符串为列表
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" not in item:
                    continue
                name, _, value = item.partition("=")
                name = name.strip()
                value = value.strip()
                if not name:
                    continue
                all_cookies.append({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                })
        if all_cookies:
            log.info(f"已加载 {len(all_cookies)} 个站点 cookie，覆盖 {len(site_cookies)} 个域名")
        return all_cookies

    def _current_metrics(self) -> CrawlMetrics:
        metrics = self._session_metrics.get() or self._metrics
        if metrics is None:
            raise RuntimeError("crawl metrics are only available during a Crawl session")
        return metrics

    async def start(self):
        """启动 crawl4ai 引擎"""
        # 构建站点 cookie 列表
        site_cookies = self._build_site_cookies()

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
        self._crawler = AsyncWebCrawler(config=browser_cfg)
        await self._crawler.start()
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
        if not self._crawler:
            await self.start()

        self._session_metrics.set(CrawlMetrics())
        seen: Set[str] = set()
        visited: Set[str] = {url}
        events: asyncio.Queue[dict | None] = asyncio.Queue()

        session_task = asyncio.create_task(
            self._run_crawl_session(
                root_url=url,
                visited=visited,
                events=events,
                seen=seen,
                depth=depth,
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
            await session_task
            self._session_metrics.set(None)

    async def _run_crawl_session(
        self,
        root_url: str,
        visited: Set[str],
        events: asyncio.Queue[dict | None],
        seen: Set[str],
        depth: int,
    ) -> None:
        try:
            batch: list[tuple[str, int]] = [(root_url, depth)]
            while batch:
                next_batch: list[tuple[str, int]] = []
                grouped = self._group_urls_by_depth(batch)

                for remaining_depth, urls in grouped:
                    await self._crawl_url_batch(
                        urls=urls,
                        remaining_depth=remaining_depth,
                        visited=visited,
                        next_batch=next_batch,
                        events=events,
                        seen=seen,
                    )
                batch = next_batch

        finally:
            metrics = self._current_metrics()
            await events.put({
                "type": "done",
                "total": metrics.magnets_found,
                "url": root_url,
                "metrics": metrics.as_dict(),
            })
            await events.put(None)

    @staticmethod
    def _group_urls_by_depth(batch: list[tuple[str, int]]) -> list[tuple[int, list[str]]]:
        grouped: dict[int, list[str]] = {}
        for url, depth in batch:
            grouped.setdefault(depth, []).append(url)
        return sorted(grouped.items(), key=lambda item: item[0], reverse=True)

    async def _crawl_url_batch(
        self,
        urls: list[str],
        remaining_depth: int,
        visited: Set[str],
        next_batch: list[tuple[str, int]],
        events: asyncio.Queue[dict | None],
        seen: Set[str],
    ) -> None:
        if not urls:
            return

        metrics = self._current_metrics()
        for url in urls:
            metrics.pages_crawled += 1
            await events.put({
                "type": "progress",
                "msg": "正在爬取...",
                "url": url,
                "depth": remaining_depth,
            })

        async for source_url, result, error in self._fetch_many_stream(urls):
            if error is not None:
                metrics.errors += 1
                log.warning("页面加载失败: %s - %s", source_url, error)
                await events.put({"type": "error", "msg": str(error), "url": source_url})
                continue

            await self._handle_crawl_result(
                result=result,
                source_url=source_url,
                remaining_depth=remaining_depth,
                visited=visited,
                next_batch=next_batch,
                events=events,
                seen=seen,
            )

    @property
    def _worker_count(self) -> int:
        return max(1, min(self._config.concurrency, 8))

    async def _handle_crawl_result(
        self,
        result,
        source_url: str,
        remaining_depth: int,
        visited: Set[str],
        next_batch: list[tuple[str, int]],
        events: asyncio.Queue[dict | None],
        seen: Set[str],
    ) -> None:
        metrics = self._current_metrics()
        result_url = getattr(result, "url", source_url) or source_url
        if not getattr(result, "success", False):
            metrics.errors += 1
            msg = getattr(result, "error_message", "") or "页面加载失败"
            await events.put({"type": "error", "msg": msg, "url": result_url})
            return

        items = self._extract_page_items(result, source_url=result_url)
        new_count = 0
        for item in items:
            hash_key = item["hash"]
            async with self._seen_lock:
                if hash_key in seen:
                    continue
                seen.add(hash_key)
            new_count += 1
            metrics.magnets_found += 1
            await events.put({"type": "found", "item": item})

        await events.put({
            "type": "progress",
            "msg": f"发现 {new_count} 个新磁力",
            "url": result_url,
            "metrics": metrics.as_dict(),
        })

        if remaining_depth <= 1:
            return

        detail_links = self._extract_detail_links(result_url, result.links)
        claimed = await self._claim_unvisited_links(detail_links, visited)
        if claimed:
            await events.put({
                "type": "progress",
                "msg": f"并发排队 {len(claimed)} 个详情页",
                "url": result_url,
                "depth": remaining_depth,
            })

        for link in claimed:
            next_batch.append((link, remaining_depth - 1))

    async def _fetch_many_stream(self, urls: list[str]):
        admitted: list[str] = []
        for url in urls:
            try:
                await self._target_admission.admit_redirect_chain(url)
            except URLValidationError as exc:
                yield url, None, exc
                continue
            admitted.append(url)

        if not admitted:
            return

        try:
            result_stream = await self._crawler.arun_many(
                admitted,
                config=self._build_run_config(stream=True),
                dispatcher=self._build_dispatcher(),
            )
            if hasattr(result_stream, "__aiter__"):
                async for result in result_stream:
                    yield getattr(result, "url", "") or "", result, None
            else:
                for result in result_stream:
                    yield getattr(result, "url", "") or "", result, None
        except Exception as exc:
            log.warning("arun_many 批量抓取失败，回退到单页抓取: %s", exc)
            for url in admitted:
                result = await self._fetch_with_retry(url)
                if result is None:
                    yield url, None, RuntimeError("页面加载失败")
                else:
                    yield getattr(result, "url", url) or url, result, None

    def _build_dispatcher(self) -> MemoryAdaptiveDispatcher:
        return MemoryAdaptiveDispatcher(
            memory_threshold_percent=85.0,
            critical_threshold_percent=92.0,
            recovery_threshold_percent=80.0,
            max_session_permit=self._worker_count,
            rate_limiter=RateLimiter(
                base_delay=(0.1, 0.4),
                max_delay=8.0,
                max_retries=2,
            ),
            monitor=None,
        )

    async def _fetch_with_retry(self, url: str):
        for retry_count in range(3):
            try:
                await self._target_admission.admit_redirect_chain(url)
                run_cfg = self._build_run_config()
                result = await self._crawler.arun(url=url, config=run_cfg)
                if result.success:
                    return result
                raise RuntimeError(getattr(result, "error_message", "") or "页面加载失败")
            except URLValidationError:
                raise
            except Exception as e:
                if retry_count >= 2:
                    log.warning(f"页面加载最终失败: {url} - {e}")
                    return None
                self._current_metrics().retries += 1
                delay = 2 ** retry_count + random.uniform(0, 1)
                log.info(f"页面加载失败，重试 {retry_count + 1}: {url} - {e}")
                await asyncio.sleep(delay)

    def _build_run_config(self, stream: bool = False) -> CrawlerRunConfig:
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=1,
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
        )

    def _extract_page_items(self, result, source_url: str) -> List[dict]:
        content_sources: List[str] = []
        for content in (result.markdown, result.cleaned_html, result.html):
            if content:
                content_text = str(content)
                if content_text not in content_sources:
                    content_sources.append(content_text)

        items: List[dict] = []
        for text in content_sources:
            items.extend(extract_from_text(text))

        items = filter_resolution_items(items, allowed=self._config.allowed_resolutions)
        for item in items:
            item.setdefault("source_url", source_url)
        return items

    def _extract_detail_links(self, parent_url: str, links: dict | None) -> List[str]:
        if not links:
            return []

        parsed_parent = urlparse(parent_url)
        internal_links = links.get("internal", []) or []
        detail_links: List[str] = []
        seen_links: Set[str] = set()

        for link in internal_links:
            href = link.get("href", "") if isinstance(link, dict) else str(link)
            if not href or href in seen_links:
                continue

            href = urljoin(parent_url, href)
            parsed = urlparse(href)
            if parsed_parent.netloc and parsed.netloc and parsed_parent.netloc != parsed.netloc:
                continue

            path = parsed.path.lower()
            query = parsed.query.lower()
            is_detail = any(token in path for token in (
                "/details/", "/detail/", "/torrent/", "/view/", "/resource/", "/movie/", "/subject/",
            )) or any(token in query for token in ("id=", "tid=", "movie_id=", "detail="))

            if not is_detail:
                continue

            seen_links.add(href)
            detail_links.append(href)
            if len(detail_links) >= self._config.max_detail_links:
                break

        return detail_links

    async def _claim_unvisited_links(self, links: List[str], visited: Set[str]) -> List[str]:
        claimed: List[str] = []
        for link in links:
            try:
                await self.admit_url(link)
            except URLValidationError:
                log.warning("跳过不安全的详情页链接: %s", link)
                continue
            async with self._visited_lock:
                if link in visited:
                    continue
                visited.add(link)
                claimed.append(link)
        return claimed
