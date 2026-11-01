"""站点走查：从一个根 URL 走到磁力所在的页面，边走边交结果。

职责边界：
- **不做磁力解析**（那是 ``magnet_sources`` 的事），只负责"哪些页面值得取、怎么取";
- **不做 HTTP/浏览器细节**（那是 ``page_fetch`` 的事），只负责"先取根页、再顺着详情页走";
- 产出的是逐页事件流：根页先出，详情页谁先回来谁先出。调用方（``crawler``）
  拿到一页就能立刻抽磁力、立刻往外吐，不必等整轮。

链接判定用"路径段 + 数字 ID"而不是 `?id=` 这类宽匹配：
宽匹配会把分页/排序链接也当详情页，一次翻出几百个请求。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.page_fetch import FailureReason, PageFetch, PageFetcher

log = logging.getLogger(__name__)

#: 详情页路径：/details/123、/torrent/abc、/view/9 ...（不含 /browse/、/search/ 这类列表页）
DETAIL_PATH_RE = re.compile(
    r"/(?:details?|torrent|view|resource|movie|subject|release|thread|topic)/[^/?#]+",
    re.IGNORECASE,
)
#: 兜底：查询串里的显式详情参数（只在路径没有标题化段时使用）
DETAIL_QUERY_RE = re.compile(r"[?&](?:id|tid|movie_id|detail|aid)=\d+", re.IGNORECASE)
#: 列表/导航路径：这些即使带 id 也不是一条资源
LISTING_PATH_RE = re.compile(
    r"/(?:browse|search|category|categories|tag|tags|page|user|users|login|register)/",
    re.IGNORECASE,
)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


@dataclass
class WalkerPage:
    """走查产出的一页（成功或带原因失败）。"""

    url: str
    status: int = 0
    html: str = ""
    via: str = "http"
    elapsed_ms: int = 0
    reason: FailureReason | None = None
    message: str = ""

    @classmethod
    def from_fetch(cls, page: PageFetch) -> "WalkerPage":
        return cls(
            url=page.url,
            status=page.status,
            html=page.html,
            via=page.via,
            elapsed_ms=page.elapsed_ms,
            reason=page.reason,
            message=page.message,
        )

    @property
    def ok(self) -> bool:
        return self.reason is None

    def as_event(self) -> dict[str, Any]:
        return {
            "kind": "page",
            "url": self.url,
            "status": self.status,
            "html": self.html,
            "via": self.via,
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason.value if self.reason else None,
            "message": self.message,
            "success": self.ok,
        }


def is_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if LISTING_PATH_RE.search(path):
        return False
    if DETAIL_PATH_RE.search(path):
        return True
    return bool(DETAIL_QUERY_RE.search("?" + (parsed.query or "")))


def extract_detail_links(html: str, base_url: str, *, limit: int | None = None) -> list[str]:
    """按文档顺序抽出去重后的详情页链接；只保留同站 http(s)。"""
    base_host = (urlparse(base_url).hostname or "").lower()
    seen: set[str] = set()
    links: list[str] = []
    for raw in HREF_RE.findall(html or ""):
        href = raw.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "data:")):
            continue
        candidate = urldefrag(urljoin(base_url, href))[0]
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            continue
        if (parsed.hostname or "").lower() != base_host:
            continue
        if candidate == base_url or candidate in seen:
            continue
        if not is_detail_url(candidate):
            continue
        seen.add(candidate)
        links.append(candidate)
        if limit is not None and len(links) >= limit:
            break
    return links


class MagnetPageWalker:
    """根页 → 详情页的事件流。"""

    def __init__(
        self,
        *,
        root_url: str,
        depth: int,
        config: CrawlerConfig,
        fetcher: PageFetcher,
        admission: Any,
        max_pages: int | None = None,
    ) -> None:
        self.root_url = root_url
        self.depth = max(1, int(depth))
        self._config = config
        self._fetcher = fetcher
        self._admission = admission
        self._max_links = config.max_detail_links if max_pages is None else max(0, int(max_pages))
        # 升级阶梯：浏览器很贵（每页 1–10s），只在真的别无他法时用。
        #   root 走 HTTP 成功  → 详情页纯 HTTP；整轮一条详情都没成，才花一次浏览器去确认
        #   root 走浏览器成功  → 站点确实需要 JS，后续页面都走浏览器
        # 这样「HTTP 能通」的站点一次浏览器都不会开，失败站点才付这笔钱。
        self._browser_all = False
        self._detail_ok = 0
        self._seen: set[str] = {root_url}
        self.errors: list[dict[str, str]] = []
        # 准入按 host 做一次就够：详情链接已经被 `extract_detail_links` 限成同站，
        # 同一个 host 解析 57 次只会拖慢首条，不会更安全。
        self._admitted_hosts: set[str] = set()
        self._admit_locks: dict[str, asyncio.Lock] = {}

    async def stream(self) -> AsyncGenerator[dict[str, Any], None]:
        root = await self._fetch_root()
        # 用户复制的就是详情页时，不再顺着「相关资源」扩散：
        # 那会把一次「这一条」变成几十条无关请求。
        followable = root.ok and self._max_links > 0 and not is_detail_url(self.root_url)
        level_urls = (
            extract_detail_links(root.html, root.url, limit=self._max_links) if followable else []
        )
        root_event = root.as_event()
        root_event["is_root"] = True
        # 根页常常只是列表页：告诉调用方「这一页没有磁力但有 N 个详情页」，
        # 这样「列表页 0 磁力」不会被误报成一次失败。
        root_event["detail_links"] = len(level_urls)
        yield root_event
        if not root.ok or not level_urls:
            return

        for link in level_urls:
            self._seen.add(link)
        level = 1
        while level_urls and level <= self.depth:
            next_level: list[str] = []
            failed: list[str] = []
            async for page in self._fetch_many(level_urls):
                yield page.as_event()
                if not page.ok:
                    failed.append(page.url)
                if not page.ok or level >= self.depth:
                    continue
                for link in extract_detail_links(page.html, page.url):
                    if len(self._seen) > self._max_links:
                        break
                    if link in self._seen:
                        continue
                    self._seen.add(link)
                    next_level.append(link)
            if level == 1 and failed and not self._browser_all and self._detail_ok == 0:
                # 整轮一条详情都没取成：这才是「可能真的需要 JS」的时刻，
                # 花一次浏览器去确认；确认没用就立刻收手，不再逐页试。
                async for rescued in self._browser_rescue(failed):
                    yield rescued
            level_urls = next_level
            level += 1

    # ── 单页 ────────────────────────────────

    async def _admit(self, url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if host in self._admitted_hosts:
            return url
        lock = self._admit_locks.setdefault(host, asyncio.Lock())
        async with lock:
            if host in self._admitted_hosts:
                return url
            admitted = await self._admission.admit(url)
            self._admitted_hosts.add(host)
            return admitted

    async def _fetch_root(self) -> WalkerPage:
        try:
            admitted = await self._admit(self.root_url)
        except Exception as exc:
            return WalkerPage(
                url=self.root_url,
                reason=FailureReason.UNSAFE_URL,
                message=str(exc)[:300],
            )
        return WalkerPage.from_fetch(await self._fetch(admitted, is_root=True))

    def _allow_browser(self, *, is_root: bool = False) -> bool:
        return self._browser_all or is_root

    async def _fetch(
        self, url: str, *, is_root: bool = False, force_browser: bool = False
    ) -> PageFetch:
        allow = True if force_browser else self._allow_browser(is_root=is_root)
        page = await self._fetcher.fetch(url, allow_browser=allow)
        if is_root and page.ok and page.via == "browser":
            self._browser_all = True
        if page.ok and not is_root:
            self._detail_ok += 1
        return page

    async def _browser_rescue(self, failed: list[str]) -> AsyncGenerator[dict[str, Any], None]:
        """整轮失败后的一次确认：浏览器真能拿到内容，就把失败页重取一遍。"""
        if not failed:
            return
        probe_url, rest = failed[0], failed[1:]
        probe = await self._fetch(probe_url, force_browser=True)
        yield probe.as_event()
        if not probe.ok:
            return
        self._browser_all = True
        if not rest:
            return
        async for page in self._fetch_many(rest):
            yield page.as_event()

    async def _fetch_one(self, url: str) -> WalkerPage:
        try:
            admitted = await self._admit(url)
        except Exception as exc:
            return WalkerPage(
                url=url,
                reason=FailureReason.UNSAFE_URL,
                message=str(exc)[:300],
            )
        return WalkerPage.from_fetch(await self._fetch(admitted))

    async def _fetch_many(self, urls: list[str]) -> AsyncGenerator[WalkerPage, None]:
        """并发取页，但**谁先回来谁先交**：首条不必等最后一页。"""
        tasks = [asyncio.create_task(self._fetch_one(url)) for url in urls]
        try:
            for finished in asyncio.as_completed(tasks):
                yield await finished
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
