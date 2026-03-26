"""
Playwright 爬虫引擎
- playwright-stealth 绕过反爬
- 6 种磁力提取策略（href / innerHTML / onclick / script / 点击加载更多 / 滚动懒加载）
- domcontentloaded + sleep，避免 SPA networkidle 超时
- context.close() 在 try/finally 中，确保异常时资源释放
- stop() 使用 try/finally，防止 playwright 进程泄漏
- 深度爬取直接顺序执行，不引入 Semaphore（递归生成器无需并发控制）
"""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import AsyncGenerator, List, Optional, Set
from urllib.parse import urljoin, urlparse

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright_stealth import stealth_async

from config import settings
from models import MagnetItem

MAGNET_RE = re.compile(
    r'magnet:\?xt=urn:btih:[a-fA-F0-9]{32,64}[^\s\'"<>]*',
    re.IGNORECASE,
)
HASH_RE = re.compile(r'btih:([a-fA-F0-9]{32,64})', re.IGNORECASE)


def _parse_magnet(raw: str) -> Optional[MagnetItem]:
    raw = raw.strip().rstrip("'\"")
    m   = HASH_RE.search(raw)
    if not m:
        return None
    btih     = m.group(1).upper()
    dn_match = re.search(r'[?&]dn=([^&]+)', raw)
    name     = urllib.parse.unquote_plus(dn_match.group(1)) if dn_match else f"Unknown_{btih[:8]}"
    return MagnetItem(hash=btih, name=name, magnet=raw, source_url="")


def _extract_from_text(text: str) -> List[MagnetItem]:
    items, seen = [], set()
    for raw in MAGNET_RE.findall(text):
        item = _parse_magnet(raw)
        if item and item.hash not in seen:
            seen.add(item.hash)
            items.append(item)
    return items


async def _extract_from_page(page: Page, source_url: str) -> List[MagnetItem]:
    seen: Set[str]        = set()
    results: List[MagnetItem] = []

    def _add(item: Optional[MagnetItem]):
        if item and item.hash not in seen:
            seen.add(item.hash)
            item.source_url = source_url
            results.append(item)

    # 策略1: href 属性
    try:
        links = await page.eval_on_selector_all(
            'a[href^="magnet:"]', 'els => els.map(e => e.href)'
        )
        for href in links:
            _add(_parse_magnet(href))
    except Exception:
        pass

    # 策略2: 完整渲染后 HTML
    try:
        for item in _extract_from_text(await page.content()):
            _add(item)
    except Exception:
        pass

    # 策略3: onclick / data-* 属性
    try:
        attrs = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('[onclick],[data-magnet],[data-src]'))
                .flatMap(el => [
                    el.getAttribute('onclick') || '',
                    el.getAttribute('data-magnet') || '',
                    el.getAttribute('data-src') || '',
                ]);
        }""")
        for val in attrs:
            for item in _extract_from_text(val):
                _add(item)
    except Exception:
        pass

    # 策略4: script 标签内嵌数据
    try:
        scripts = await page.eval_on_selector_all(
            'script', 'els => els.map(e => e.textContent)'
        )
        for s in scripts:
            for item in _extract_from_text(s or ""):
                _add(item)
    except Exception:
        pass

    # 策略5: 触发"加载更多"按钮
    try:
        for sel in ['button:has-text("加载更多")', 'button:has-text("Load More")', '.pagination a']:
            for btn in (await page.query_selector_all(sel))[:3]:
                try:
                    await btn.click(timeout=2000)
                    await page.wait_for_load_state("domcontentloaded", timeout=4000)
                    for item in _extract_from_text(await page.content()):
                        _add(item)
                except Exception:
                    pass
    except Exception:
        pass

    # 策略6: 滚动触发懒加载
    try:
        await page.evaluate("""async () => {
            await new Promise(resolve => {
                let scrolled = 0;
                const timer = setInterval(() => {
                    window.scrollBy(0, 600);
                    scrolled += 600;
                    if (scrolled >= document.body.scrollHeight) {
                        clearInterval(timer); resolve();
                    }
                }, 200);
                setTimeout(() => { clearInterval(timer); resolve(); }, 5000);
            });
        }""")
        await asyncio.sleep(1)
        for item in _extract_from_text(await page.content()):
            _add(item)
    except Exception:
        pass

    return results


def _get_same_domain_links(html: str, base_url: str) -> List[str]:
    base    = urlparse(base_url)
    pattern = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
    seen, links = set(), []
    for href in pattern.findall(html):
        full   = urljoin(base_url, href)
        parsed = urlparse(full)
        if (
            parsed.netloc == base.netloc
            and parsed.scheme in ("http", "https")
            and full not in seen
            and not any(ext in full for ext in ('.jpg', '.png', '.css', '.js', '.ico', '.gif'))
        ):
            seen.add(full)
            links.append(full)
    return links[:20]


class MagnetCrawler:

    def __init__(self):
        self._playwright = None
        self._browser    = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser    = await self._playwright.chromium.launch(
            headless = settings.CRAWLER_HEADLESS,
            args     = [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--lang=zh-CN,zh",
            ],
        )

    async def stop(self):
        # try/finally 确保 playwright 进程在 browser.close() 异常时也能停止
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._playwright:
                await self._playwright.stop()

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]:
        if not self._browser:
            await self.start()

        context: BrowserContext = await self._browser.new_context(
            viewport    = {"width": 1920, "height": 1080},
            user_agent  = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale      = "zh-CN",
            timezone_id = "Asia/Shanghai",
            java_script_enabled = True,
        )

        async def block_media(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", block_media)

        visited:   Set[str] = set()
        all_found: Set[str] = set()

        # context.close() 在 finally，确保页面异常时也能释放
        try:
            async def _crawl_single(target_url: str, current_depth: int):
                if target_url in visited:
                    return
                visited.add(target_url)

                page = await context.new_page()
                try:
                    await stealth_async(page)
                    yield {"type": "progress", "msg": "正在爬取...", "url": target_url}

                    # domcontentloaded 避免 SPA 永远不会触发 networkidle
                    await page.goto(
                        target_url,
                        wait_until = "domcontentloaded",
                        timeout    = settings.CRAWLER_TIMEOUT * 1000,
                    )
                    await asyncio.sleep(2.0)

                    items = await _extract_from_page(page, target_url)
                    for item in items:
                        if item.hash not in all_found:
                            all_found.add(item.hash)
                            yield {"type": "found", "item": item.model_dump()}

                    yield {"type": "progress", "msg": f"发现 {len(items)} 个磁力", "url": target_url}

                    # 深度爬取：顺序递归，无需 Semaphore
                    if current_depth < depth:
                        html      = await page.content()
                        sub_links = _get_same_domain_links(html, target_url)
                        for link in sub_links[:10]:
                            async for msg in _crawl_single(link, current_depth + 1):
                                yield msg

                except Exception as e:
                    yield {"type": "error", "msg": str(e), "url": target_url}
                finally:
                    await page.close()

            async for msg in _crawl_single(url, 1):
                yield msg

            yield {"type": "done", "total": len(all_found), "url": url}

        finally:
            await context.close()


crawler = MagnetCrawler()
