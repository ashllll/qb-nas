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
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, List, Protocol, Set

from magnet_harvester.config import CrawlerConfig, settings
from magnet_harvester.magnet_sources import (
    MagnetSourceExtractor,
    filter_resolution_items as _filter_resolution_items,
)
from magnet_harvester.scrapling_spider import MagnetSpider
from magnet_harvester.services.site_auth import SiteAuth
from magnet_harvester.utils.url_validator import (
    CrawlTargetAdmission,
)
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)


class CrawlMetrics:
    def __init__(self):
        self.start_time = time.monotonic()
        self.pages_crawled = 0
        self.magnets_found = 0
        self.errors = 0

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start_time

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
class _PageResult:
    url: str
    success: bool
    html: str = ""
    cleaned_html: str = ""
    markdown: str = ""
    error_message: str = ""

    @classmethod
    def from_spider_item(cls, item: dict[str, Any]) -> "_PageResult":
        return cls(
            url=str(item.get("url", "")),
            success=bool(item.get("success", False)),
            html=str(item.get("html", "")),
            cleaned_html=str(item.get("cleaned_html", "")),
            markdown=str(item.get("markdown", "")),
            error_message=str(item.get("error_message", "")),
        )


class MagnetCrawler:
    """爬虫入口 — 使用 Scrapling 引擎

    公共接口：
        start()                — 启动浏览器引擎
        stop()                 — 关闭浏览器引擎
        crawl(url, depth=1)    — 异步生成器，产出事件消息
    """

    def __init__(
        self,
        config: CrawlerConfig | None = None,
        target_admission: CrawlTargetAdmission | None = None,
        site_auth: BrowserCookieProvider | None = None,
    ):
        self._config = config if config is not None else settings.crawler
        self._started = False
        self._active_spiders: set[MagnetSpider] = set()
        self._session_metrics: ContextVar[CrawlMetrics | None] = ContextVar(
            "crawl_session_metrics",
            default=None,
        )
        self._target_admission = target_admission or CrawlTargetAdmission(
            allow_fake_ip=self._config.allow_fake_ip
        )
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

    async def start(self) -> None:
        """标记爬虫可用；浏览器会话由每个 Scrapling Spider 管理。"""
        self._started = True
        log.info("Scrapling Spider 爬虫已就绪")

    async def stop(self) -> None:
        """停止活跃 Spider；Scrapling 引擎负责关闭各自浏览器会话。"""
        for spider in tuple(self._active_spiders):
            spider.request_stop()
        self._started = False
        if self._target_admission is not None and hasattr(self._target_admission, "close"):
            try:
                await self._target_admission.close()
            except Exception as e:
                log.warning(f"关闭 CrawlTargetAdmission 时出错: {e}")
        log.info("Scrapling Spider 爬虫已关闭")

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]:
        """爬取 URL 并提取磁力链接

        产出事件:
            {"type": "found", "item": {...}}     — 发现磁力链接
            {"type": "progress", ...}             — 进度信息
            {"type": "error", ...}                — 错误信息
            {"type": "done", "total": N, ...}     — 爬取完成
        """
        url = await self.admit_url(url)
        if not self._started:
            await self.start()

        effective_depth = self._clamp_depth(depth)
        self._session_metrics.set(CrawlMetrics())
        seen: Set[str] = set()
        events = self._make_event_queue()

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
        timed_out_metrics: CrawlMetrics | None = None
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
                    timed_out_metrics = self._current_metrics()
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
        if timed_out_metrics is not None:
            yield {
                "type": "done",
                "total": timed_out_metrics.magnets_found,
                "url": url,
                "metrics": timed_out_metrics.as_dict(),
            }

    async def _finish_session_task(self, session_task: asyncio.Task) -> None:
        if not session_task.done():
            session_task.cancel()
        await asyncio.gather(session_task, return_exceptions=True)

    def _make_event_queue(self) -> asyncio.Queue[dict | None]:
        return asyncio.Queue(maxsize=max(32, min(self._config.concurrency, 8) * 8))

    async def _run_crawl_session(
        self,
        root_url: str,
        events: asyncio.Queue[dict | None],
        seen: Set[str],
        depth: int,
    ) -> None:
        # 保存 CrawlMetrics 引用, 防止 finally 块中 ContextVar 已被外层设为 None
        metrics: CrawlMetrics = self._current_metrics()
        spider: MagnetSpider | None = None
        cancelled = False
        try:
            await events.put(
                {"type": "progress", "msg": "正在爬取...", "url": root_url, "depth": depth}
            )
            spider = self._build_spider(root_url, depth)

            async def emit_spider_error(url: str, message: str) -> None:
                metrics.errors += 1
                await events.put({"type": "error", "msg": message, "url": url})

            spider.set_error_sink(emit_spider_error)
            self._active_spiders.add(spider)
            async for item in spider.stream():
                if item.get("kind") != "page":
                    continue
                result = _PageResult.from_spider_item(item)
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
            for error in spider.errors:
                metrics.errors += 1
                await events.put(
                    {
                        "type": "error",
                        "msg": error["message"],
                        "url": error["url"],
                    }
                )
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            log.exception("深爬会话异常: %s", exc)
            metrics.errors += 1
            await events.put({"type": "error", "msg": str(exc), "url": root_url})
        finally:
            if not cancelled:
                await events.put(
                    {
                        "type": "done",
                        "total": metrics.magnets_found,
                        "url": root_url,
                        "metrics": metrics.as_dict(),
                    }
                )
                await events.put(None)
            if spider is not None:
                self._active_spiders.discard(spider)

    def _build_spider(self, root_url: str, depth: int) -> MagnetSpider:
        return MagnetSpider(
            root_url=root_url,
            depth=depth,
            config=self._config,
            target_admission=self._target_admission,
            cookies=self._site_auth.browser_cookies(),
        )

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

    def _extract_page_items(self, result, source_url: str) -> List[dict]:
        return self._magnet_sources.from_page_result(result, source_url=source_url)
