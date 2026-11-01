"""MagnetCrawler v3.1 — HTTP 优先的取页 → 磁力事件流。

对外的接口一个字没改（``crawl(url, depth)`` 产出 ``found/progress/error/done``），
内部换成了 **HTTP 优先 + 按需升级浏览器** 的走查：

    MagnetCrawler.crawl()
      └─ MagnetPageWalker.stream()        # 根页 → 详情页，谁先回来谁先交
           └─ PageFetcher.fetch()         # HTTP(FetcherSession, chrome) → 必要时浏览器
                └─ Scrapling / curl_cffi

三个必须守住的性质：
1. **边找到边吐**：每抽到一条磁力就 ``yield``，不等整轮；
2. **失败有原因**：``error.reason`` 取自 ``FailureReason`` 枚举；
3. **来源不丢**：``source_url`` 永远是用户复制的根 URL，``detail_url`` 是磁力所在页。
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, List, Protocol, Set

from magnet_harvester.config import CrawlerConfig, settings
from magnet_harvester.dynamic_page import DynamicPagePolicy
from magnet_harvester.magnet_sources import (
    MagnetSourceExtractor,
    filter_resolution_items as _filter_resolution_items,
)
from magnet_harvester.page_fetch import FailureReason, PageFetcher
from magnet_harvester.services.site_auth import SiteAuth
from magnet_harvester.site_walker import MagnetPageWalker
from magnet_harvester.utils.bg_tasks import BGTaskManager
from magnet_harvester.utils.url_validator import (
    CrawlTargetAdmission,
)

log = logging.getLogger(__name__)


class CrawlMetrics:
    """一次抓取的读数。字段名即交给测量脚本/中心服务的读数名。"""

    def __init__(self):
        self.start_time = time.monotonic()
        self.pages_crawled = 0
        self.magnets_found = 0
        self.errors = 0
        self.first_item_ms: int | None = None
        self.browser_used = False
        self.reasons: dict[str, int] = {}
        self.pages: list[dict[str, Any]] = []

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start_time

    def note_error(self, reason: str) -> None:
        self.errors += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def as_dict(self) -> dict:
        return {
            "elapsed_sec": round(self.elapsed, 2),
            "pages_crawled": self.pages_crawled,
            "magnets_found": self.magnets_found,
            "errors": self.errors,
            "first_item_ms": self.first_item_ms,
            "browser_used": self.browser_used,
            "errors_by_reason": dict(self.reasons),
            "pages": list(self.pages),
        }


class CrawlPhase(Protocol):
    """Protocol for crawl adapters — implemented by MagnetCrawler."""

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]: ...
    async def admit_url(self, url: str) -> str: ...


def filter_resolution_items(items: List[dict], allowed: tuple = ()) -> List[dict]:
    return _filter_resolution_items(items, allowed=allowed)


class BrowserCookieProvider(Protocol):
    def browser_cookies(self) -> list[dict]: ...


@dataclass
class _WalkerPage:
    url: str
    success: bool
    html: str = ""
    via: str = "http"
    status: int = 0
    elapsed_ms: int = 0
    reason: str | None = None
    message: str = ""
    detail_links: int = 0
    is_root: bool = False

    @classmethod
    def from_walker_event(cls, item: dict[str, Any]) -> "_WalkerPage":
        return cls(
            url=str(item.get("url", "")),
            success=bool(item.get("success", False)),
            html=str(item.get("html", "")),
            via=str(item.get("via", "http")),
            status=int(item.get("status", 0) or 0),
            elapsed_ms=int(item.get("elapsed_ms", 0) or 0),
            reason=item.get("reason"),
            message=str(item.get("message", "")),
            detail_links=int(item.get("detail_links", 0) or 0),
            is_root=bool(item.get("is_root", False)),
        )


class MagnetCrawler:
    """爬虫入口 — HTTP 优先的取页引擎。

    公共接口：
        start()                — 预热 HTTP 会话（毫秒级）
        stop()                 — 关闭 HTTP/浏览器会话
        crawl(url, depth=1)    — 异步生成器，产出事件消息
    """

    def __init__(
        self,
        config: CrawlerConfig | None = None,
        target_admission: CrawlTargetAdmission | None = None,
        site_auth: BrowserCookieProvider | None = None,
        fetcher: PageFetcher | None = None,
    ):
        self._config = config if config is not None else settings.crawler
        self._started = False
        self._active_walkers: set[MagnetPageWalker] = set()
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
        self._fetcher = fetcher or PageFetcher(
            config=self._config,
            target_admission=self._target_admission,
            page_preparer=DynamicPagePolicy(self._config),
            cookies=self._site_auth.browser_cookies(),
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
        """预热 HTTP 会话；浏览器会话仍然只在真的需要时才启动。"""
        await self._fetcher.start()
        self._started = True
        log.info("取页引擎已就绪（HTTP 优先，浏览器按需升级）")

    async def stop(self) -> None:
        """关闭长期持有的 HTTP/浏览器会话。"""
        await self._fetcher.close()
        self._started = False
        if self._target_admission is not None and hasattr(self._target_admission, "close"):
            try:
                await self._target_admission.close()
            except Exception as e:
                log.warning(f"关闭 CrawlTargetAdmission 时出错: {e}")
        log.info("取页引擎已关闭")

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]:
        """爬取 URL 并提取磁力链接

        产出事件:
            {"type": "found", "item": {...}}     — 发现磁力链接（边找到边吐）
            {"type": "progress", ...}             — 进度信息
            {"type": "error", "reason": "..."}    — 失败（含枚举原因）
            {"type": "done", "total": N, "metrics": {...}} — 爬取完成
        """
        try:
            url = await self.admit_url(url)
        except Exception as exc:
            # 不安全/解析不了的 URL 也要走事件流，而不是把异常扔给调用方。
            metrics = CrawlMetrics()
            metrics.note_error(FailureReason.UNSAFE_URL.value)
            yield {
                "type": "error",
                "msg": str(exc),
                "url": url,
                "reason": FailureReason.UNSAFE_URL.value,
            }
            yield {"type": "done", "total": 0, "url": url, "metrics": metrics.as_dict()}
            return
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

        crawl_timeout = self._crawl_timeout(effective_depth)
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
                    timed_out_metrics.note_error(FailureReason.TIMEOUT.value)
                    yield {
                        "type": "error",
                        "msg": f"爬取超时 ({crawl_timeout}s)",
                        "url": url,
                        "reason": FailureReason.TIMEOUT.value,
                    }
                    break
                if msg is None:
                    break
                yield msg
        except Exception as e:
            log.error(f"爬取过程异常: {e}")
            yield {
                "type": "error",
                "msg": str(e),
                "url": url,
                "reason": FailureReason.PARSE_ERROR.value,
            }
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

    def _crawl_timeout(self, depth: int) -> int:
        """整轮上限：单页超时 × 层数 × 2，最低 60s（正常一轮是秒级，这是兜底）。"""
        return max(60, depth * self._config.timeout * 2)

    async def _finish_session_task(self, session_task: asyncio.Task) -> None:
        if not session_task.done():
            session_task.cancel()
        await asyncio.gather(session_task, return_exceptions=True)

    def _make_event_queue(self) -> asyncio.Queue[dict | None]:
        return asyncio.Queue(maxsize=max(32, min(self._config.concurrency, 8) * 8))

    def _build_walker(self, root_url: str, depth: int) -> MagnetPageWalker:
        return MagnetPageWalker(
            root_url=root_url,
            depth=depth,
            config=self._config,
            fetcher=self._fetcher,
            admission=self._target_admission,
        )

    async def _run_crawl_session(
        self,
        root_url: str,
        events: asyncio.Queue[dict | None],
        seen: Set[str],
        depth: int,
    ) -> None:
        # 保存 CrawlMetrics 引用, 防止 finally 块中 ContextVar 已被外层设为 None
        metrics: CrawlMetrics = self._current_metrics()
        walker: MagnetPageWalker | None = None
        cancelled = False
        try:
            await events.put(
                {"type": "progress", "msg": "正在取页...", "url": root_url, "depth": depth}
            )
            walker = self._build_walker(root_url, depth)
            self._active_walkers.add(walker)
            async for item in walker.stream():
                if item.get("kind") != "page":
                    continue
                page = _WalkerPage.from_walker_event(item)
                try:
                    await self._handle_walker_page(
                        page=page,
                        root_url=root_url,
                        events=events,
                        seen=seen,
                    )
                except Exception as exc:
                    metrics.note_error(FailureReason.PARSE_ERROR.value)
                    log.exception("处理页面结果失败: %s", page.url or root_url)
                    await events.put(
                        {
                            "type": "error",
                            "msg": str(exc),
                            "url": page.url or root_url,
                            "reason": FailureReason.PARSE_ERROR.value,
                        }
                    )
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            log.exception("取页会话异常: %s", exc)
            metrics.note_error(FailureReason.PARSE_ERROR.value)
            await events.put(
                {
                    "type": "error",
                    "msg": str(exc),
                    "url": root_url,
                    "reason": FailureReason.PARSE_ERROR.value,
                }
            )
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
            if walker is not None:
                self._active_walkers.discard(walker)

    async def _handle_walker_page(
        self,
        page: _WalkerPage,
        root_url: str,
        events: asyncio.Queue[dict | None],
        seen: Set[str],
    ) -> None:
        metrics = self._current_metrics()
        metrics.pages_crawled += 1
        page_url = page.url or root_url
        metrics.browser_used = metrics.browser_used or page.via == "browser"
        row: dict[str, Any] = {
            "url": page_url,
            "via": page.via,
            "status": page.status,
            "ms": page.elapsed_ms,
            "magnets": 0,
            "reason": page.reason,
        }
        metrics.pages.append(row)

        if not page.success:
            reason = page.reason or FailureReason.HTTP_ERROR.value
            metrics.note_error(reason)
            await events.put(
                {
                    "type": "error",
                    "msg": page.message or "页面取回失败",
                    "url": page_url,
                    "reason": reason,
                }
            )
            return

        items = await asyncio.to_thread(
            self._extract_page_items,
            page.html,
            source_url=root_url,
            detail_url=page_url,
        )
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
            if metrics.first_item_ms is None:
                metrics.first_item_ms = int(metrics.elapsed * 1000)
            await events.put({"type": "found", "item": item})

        row["magnets"] = new_count
        is_root_page = page.is_root or page_url == root_url
        if not items and not (is_root_page and page.detail_links):
            # 根页本身常常只是列表页，没有磁力不算失败；详情页没有磁力才是。
            metrics.note_error(FailureReason.NO_MAGNET.value)
            row["reason"] = FailureReason.NO_MAGNET.value
            await events.put(
                {
                    "type": "error",
                    "msg": "页面里没有磁力链接",
                    "url": page_url,
                    "reason": FailureReason.NO_MAGNET.value,
                }
            )

        if new_count:
            # progress 只背轻量字段：整轮读数在 done 里给一次，
            # 否则 50 页就是 50 份越来越长的 metrics 在队列里排队。
            await events.put(
                {
                    "type": "progress",
                    "msg": f"发现 {new_count} 个新磁力",
                    "url": page_url,
                    "found": new_count,
                }
            )

    def _extract_page_items(self, html: str, *, source_url: str, detail_url: str) -> List[dict]:
        return self._magnet_sources.from_html(html, source_url=source_url, detail_url=detail_url)
