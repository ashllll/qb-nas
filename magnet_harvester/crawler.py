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
import time
from urllib.parse import urlparse
from typing import AsyncGenerator, Dict, List, Optional, Set

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from magnet_harvester.config import CrawlerConfig, settings
from magnet_harvester.magnet_parser import extract_from_text, parse_magnet

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
    """按分辨率过滤磁力列表，只保留含指定分辨率关键词的条目"""
    allowed_lower = {a.lower() for a in allowed}
    return [it for it in items if any(ar in it.get("name", "").lower() for ar in allowed_lower)]


class MagnetCrawler:
    """爬虫入口 — 使用 crawl4ai 引擎

    公共接口：
        start()                — 启动浏览器引擎
        stop()                 — 关闭浏览器引擎
        crawl(url, depth=1)    — 异步生成器，产出事件消息
    """

    def __init__(self, config: CrawlerConfig = None):
        if config is None:
            self._config = CrawlerConfig(
                timeout=settings.CRAWLER_TIMEOUT,
                max_depth=settings.CRAWLER_MAX_DEPTH,
                concurrency=settings.CRAWLER_CONCURRENCY,
                headless=settings.CRAWLER_HEADLESS,
            )
        else:
            self._config = config
        self._crawler: Optional[AsyncWebCrawler] = None
        self._metrics: Optional[CrawlMetrics] = None
        self._global_seen: Set[str] = set()

    async def start(self):
        """启动 crawl4ai 引擎"""
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
        if not self._crawler:
            await self.start()

        self._metrics = CrawlMetrics()
        self._global_seen.clear()
        visited: Set[str] = {url}
        frontier: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        events: asyncio.Queue[dict | None] = asyncio.Queue()
        workers: list[asyncio.Task] = []

        await frontier.put((url, depth))

        async def worker() -> None:
            while True:
                current_url, remaining_depth = await frontier.get()
                try:
                    await self._crawl_page(current_url, remaining_depth, visited, frontier, events)
                finally:
                    frontier.task_done()

        worker_count = max(1, min(self._config.concurrency, 8))
        for idx in range(worker_count):
            workers.append(asyncio.create_task(worker(), name=f"crawl-worker:{idx}"))

        async def finalize() -> None:
            try:
                await frontier.join()
            finally:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                await events.put({
                    "type": "done",
                    "total": self._metrics.magnets_found,
                    "url": url,
                    "metrics": self._metrics.as_dict(),
                })
                await events.put(None)

        finalize_task = asyncio.create_task(finalize(), name="crawl-finalize")

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
            await finalize_task

    async def _crawl_page(
        self,
        url: str,
        depth: int,
        visited: Set[str],
        frontier: asyncio.Queue[tuple[str, int]],
        events: asyncio.Queue[dict | None],
    ) -> None:
        self._metrics.pages_crawled += 1
        await events.put({"type": "progress", "msg": "正在爬取...", "url": url, "depth": depth})

        result = await self._fetch_with_retry(url)
        if result is None:
            self._metrics.errors += 1
            await events.put({"type": "error", "msg": "页面加载失败", "url": url})
            return

        items = self._extract_page_items(result, source_url=url)
        new_count = 0
        for item in items:
            hash_key = item["hash"]
            if hash_key in self._global_seen:
                continue
            self._global_seen.add(hash_key)
            new_count += 1
            self._metrics.magnets_found += 1
            await events.put({"type": "found", "item": item})

        await events.put({
            "type": "progress",
            "msg": f"发现 {new_count} 个新磁力",
            "url": url,
            "metrics": self._metrics.as_dict(),
        })

        if depth <= 1:
            return

        detail_links = self._extract_detail_links(url, result.links, visited)
        if detail_links:
            await events.put({
                "type": "progress",
                "msg": f"并发排队 {len(detail_links)} 个详情页",
                "url": url,
                "depth": depth,
            })

        for link in detail_links:
            visited.add(link)
            await frontier.put((link, depth - 1))

    async def _fetch_with_retry(self, url: str):
        for retry_count in range(3):
            try:
                run_cfg = CrawlerRunConfig(
                    cache_mode=CacheMode.ENABLED,
                    word_count_threshold=1,
                    verbose=False,
                    page_timeout=self._config.timeout * 1000,
                )
                result = await self._crawler.arun(url=url, config=run_cfg)
                if result.success:
                    return result
                raise RuntimeError(getattr(result, "error_message", "") or "页面加载失败")
            except Exception as e:
                if retry_count >= 2:
                    log.warning(f"页面加载最终失败: {url} - {e}")
                    return None
                self._metrics.retries += 1
                delay = 2 ** retry_count
                log.info(f"页面加载失败，重试 {retry_count + 1}: {url} - {e}")
                await asyncio.sleep(delay)

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

        items = filter_resolution_items(items)
        for item in items:
            item.setdefault("source_url", source_url)
        return items

    def _extract_detail_links(self, parent_url: str, links: dict | None, visited: Set[str]) -> List[str]:
        if not links:
            return []

        parsed_parent = urlparse(parent_url)
        internal_links = links.get("internal", []) or []
        detail_links: List[str] = []
        seen_links: Set[str] = set()

        for link in internal_links:
            href = link.get("href", "") if isinstance(link, dict) else str(link)
            if not href or href in visited or href in seen_links:
                continue

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
            if len(detail_links) >= 50:
                break

        return detail_links
