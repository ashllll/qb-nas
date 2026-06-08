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
from typing import AsyncGenerator, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

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

        visited: Set[str] = set()

        try:
            async for msg in self._crawl_single(url, depth, visited, retry_count=0):
                yield msg

            yield {
                "type": "done",
                "total": self._metrics.magnets_found,
                "url": url,
                "metrics": self._metrics.as_dict(),
            }
        except Exception as e:
            log.error(f"爬取过程异常: {e}")
            yield {"type": "error", "msg": str(e), "url": url}

    async def _crawl_single(
        self,
        url: str,
        depth: int,
        visited: Set[str],
        retry_count: int = 0,
    ) -> AsyncGenerator[dict, None]:
        """爬取单个页面及其子页面"""
        if url in visited:
            return
        visited.add(url)
        self._metrics.pages_crawled += 1

        # ── 用 crawl4ai 获取页面内容 ──
        yield {"type": "progress", "msg": "正在爬取...", "url": url, "depth": depth}

        try:
            run_cfg = CrawlerRunConfig(
                cache_mode=CacheMode.ENABLED,
                word_count_threshold=1,
                verbose=False,
                page_timeout=self._config.timeout * 1000,
            )

            result = await self._crawler.arun(url=url, config=run_cfg)

            if not result.success:
                if retry_count < 2:
                    visited.discard(url)  # 允许重试
                    self._metrics.retries += 1
                    log.info(f"页面加载失败，重试 {retry_count + 1}: {url}")
                    await asyncio.sleep(2 ** retry_count)
                    async for msg in self._crawl_single(url, depth, visited, retry_count + 1):
                        yield msg
                    return
                else:
                    yield {"type": "error", "msg": "页面加载失败", "url": url}
                    self._metrics.errors += 1
                    return

        except Exception as e:
            if retry_count < 2:
                visited.discard(url)  # 允许重试
                self._metrics.retries += 1
                log.info(f"爬取异常，重试 {retry_count + 1}: {url} - {e}")
                await asyncio.sleep(2 ** retry_count)
                async for msg in self._crawl_single(url, depth, visited, retry_count + 1):
                    yield msg
                return
            else:
                yield {"type": "error", "msg": str(e), "url": url}
                self._metrics.errors += 1
                return

        # ── 从结果中提取磁力链接 ──
        # crawl4ai 的 markdown 输出是干净的文本内容
        content_sources: List[str] = []

        # 来源1: markdown 输出（最干净）
        if result.markdown:
            content_sources.append(str(result.markdown))

        # 来源2: cleaned_html
        if result.cleaned_html:
            content_sources.append(result.cleaned_html)

        # 来源3: raw html (fallback)
        if result.html:
            content_sources.append(result.html)

        items = []
        for text in content_sources:
            items.extend(extract_from_text(text))

        # 分辨率过滤：只保留 2160p / 4k
        items = filter_resolution_items(items)

        # 全局去重
        new_count = 0
        for item in items:
            if item["hash"] not in self._global_seen:
                self._global_seen.add(item["hash"])
                new_count += 1
                self._metrics.magnets_found += 1
                yield {"type": "found", "item": item}

        yield {
            "type": "progress",
            "msg": f"发现 {new_count} 个新磁力",
            "url": url,
            "metrics": self._metrics.as_dict(),
        }

        # ── 深度爬取 ──
        if depth > 1:
            if result.links:
                internal_links = result.links.get("internal", [])
                detail_links = []
                for link in internal_links:
                    href = link.get("href", "") if isinstance(link, dict) else str(link) if hasattr(link, "href") else str(link)
                    if "/details/" in href and href not in visited:
                        detail_links.append(href)
                        if len(detail_links) >= 50:
                            break

                for idx, link in enumerate(detail_links):
                    if link not in visited:
                        yield {"type": "progress", "msg": f"爬取详情页 {idx+1}/{len(detail_links)}", "url": link, "depth": depth}
                        async for msg in self._crawl_single(link, depth - 1, visited):
                            yield msg
