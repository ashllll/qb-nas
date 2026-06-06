"""
Playwright 爬虫引擎 v2.0
- playwright-stealth 绕过反爬
- 10 种磁力提取策略（href / innerHTML / onclick / script / JSON / Base64 / iframe / 动态加载 / 懒加载）
- domcontentloaded + sleep，避免 SPA networkidle 超时
- context.close() 在 try/finally 中，确保异常时资源释放
- stop() 使用 try/finally，防止 playwright 进程泄漏
- 深度爬取直接顺序执行，不引入 Semaphore（递归生成器无需并发控制）
- 增强去重：跨页面全局哈希去重
- 磁力信息提取：名称、文件大小、文件数、 trackers
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
import time
import urllib.parse
from typing import AsyncGenerator, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright_stealth import stealth

from magnet_harvester.config import CrawlerConfig, settings
from magnet_harvester.models import MagnetItem

log = logging.getLogger(__name__)

MAGNET_RE = re.compile(
    r'magnet:\?xt=urn:btih:[a-fA-F0-9]{32,64}(?:[^\s\'"<>&\)]+)?',
    re.IGNORECASE,
)

MAGNET_FULL_RE = re.compile(
    r'magnet:\?([^"\']+)',
    re.IGNORECASE,
)

HASH_RE = re.compile(r'btih:([a-fA-F0-9]{32,64})', re.IGNORECASE)

TRACKER_RE = re.compile(r'tr=([^&\s]+)', re.IGNORECASE)

SIZE_RE = re.compile(r'xl=(?:(\d+)|size=(\d+))', re.IGNORECASE)

BASE64_MAGNET_RE = re.compile(
    r'bWFnbmV0[a-zA-Z0-9+/]{10,250}={0,2}',
    re.IGNORECASE,
)

BASE64_MIN_LENGTH = 20
BASE64_MAX_LENGTH = 300

BASE64_VALID_RE = re.compile(
    r'^[a-zA-Z0-9+/]+={0,2}$',
)

BTIH_PATTERN_RE = re.compile(
    r'btih:([a-fA-F0-9]{32,40})',
    re.IGNORECASE,
)

JSON_MAGNET_RE = re.compile(
    r'"(magnet[^\"]+)"|\'(magnet[^\']+)\'',
    re.IGNORECASE,
)

MAGNIFY_ENCODED_RE = re.compile(
    r'(?:magnet|%6D%61%67%6E%65%74)(?:%3A|%3a|:)(?:[^\s\'"<>]+)',
    re.IGNORECASE,
)


def _parse_magnet(raw: str) -> Optional[MagnetItem]:
    raw = raw.strip().rstrip("'\"").split()[0]
    m   = HASH_RE.search(raw)
    if not m:
        return None
    btih = m.group(1).upper()
    
    dn_match = re.search(r'[?&]dn=([^&]+)', raw)
    name = urllib.parse.unquote_plus(dn_match.group(1)) if dn_match else f"Unknown_{btih[:8]}"
    
    xl_match = SIZE_RE.search(raw)
    size = None
    if xl_match:
        size = xl_match.group(1) or xl_match.group(2)
    
    return MagnetItem(
        hash=btih,
        name=name,
        magnet=raw,
        source_url="",
        size=size
    )


def _try_decode_base64(text: str) -> List[str]:
    results = []
    candidates = set()
    
    for match in BASE64_MAGNET_RE.finditer(text):
        candidate = match.group()
        if BASE64_MIN_LENGTH <= len(candidate) <= BASE64_MAX_LENGTH:
            candidates.add(candidate)
    
    for candidate in candidates:
        try:
            if not BASE64_VALID_RE.match(candidate):
                continue
            
            decoded_bytes = base64.b64decode(candidate)
            decoded = decoded_bytes.decode('utf-8', errors='ignore')
            
            if not decoded or len(decoded) < 10:
                continue
            
            decoded_lower = decoded.lower()
            
            if 'magnet:' in decoded_lower or 'btih:' in decoded_lower:
                magnets = MAGNET_RE.findall(decoded)
                if magnets:
                    results.extend(magnets)
                else:
                    hash_match = BTIH_PATTERN_RE.search(decoded)
                    if hash_match:
                        magnet = f"magnet:?xt=urn:btih:{hash_match.group(1).upper()}"
                        results.append(magnet)
                    
        except binascii.Error as e:
            log.debug(f"Base64 解码失败（非法的 Base64 编码）: {candidate[:30]}... - {e}")
        except ValueError as e:
            log.debug(f"Base64 解码失败（非法字符）: {candidate[:30]}... - {e}")
        except UnicodeDecodeError as e:
            log.debug(f"UTF-8 解码失败（非 UTF-8 数据）: {candidate[:30]}... - {e}")
        except Exception as e:
            log.warning(f"Base64 解码未知错误: {candidate[:30]}... - {type(e).__name__}: {e}")
    
    return list(set(results))


def _extract_magnet_params(raw: str) -> Dict[str, str]:
    params = {}
    match = MAGNET_FULL_RE.search(raw)
    if match:
        query_string = match.group(1)
        for pair in query_string.split('&'):
            if '=' in pair:
                key, value = pair.split('=', 1)
                params[urllib.parse.unquote_plus(key)] = urllib.parse.unquote_plus(value)
    return params


def _deduplicate_magnets(items: List[MagnetItem]) -> List[MagnetItem]:
    seen = set()
    result = []
    for item in items:
        if item.hash not in seen:
            seen.add(item.hash)
            result.append(item)
    return result


def _extract_from_text(text: str) -> List[MagnetItem]:
    items, seen = [], set()
    
    for raw in MAGNET_RE.findall(text):
        item = _parse_magnet(raw)
        if item and item.hash not in seen:
            seen.add(item.hash)
            items.append(item)
    
    decoded_b64 = _try_decode_base64(text)
    for raw in decoded_b64:
        item = _parse_magnet(raw)
        if item and item.hash not in seen:
            seen.add(item.hash)
            items.append(item)
    
    for m in JSON_MAGNET_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if raw:
            item = _parse_magnet(raw)
            if item and item.hash not in seen:
                seen.add(item.hash)
                items.append(item)
    
    return items


async def _extract_from_page(page: Page, source_url: str) -> List[MagnetItem]:
    seen: Set[str]           = set()
    results: List[MagnetItem] = []

    def _add(item: Optional[MagnetItem]):
        if item and item.hash not in seen:
            seen.add(item.hash)
            item.source_url = source_url
            results.append(item)

    async def _extract_text_sources():
        sources = set()
        
        try:
            links = await page.eval_on_selector_all(
                'a[href^="magnet:"]', 'els => els.map(e => e.href)'
            )
            sources.update(links)
        except Exception as e:
            log.debug(f"href提取失败: {e}")

        try:
            sources.add(await page.content())
        except Exception as e:
            log.debug(f"HTML内容获取失败: {e}")

        try:
            attrs = await page.evaluate("""() => {
                const sources = [];
                document.querySelectorAll('[onclick],[data-magnet],[data-src],[data-href],[data-url]').forEach(el => {
                    ['onclick', 'data-magnet', 'data-src', 'data-href', 'data-url', 'data-link'].forEach(attr => {
                        const val = el.getAttribute(attr);
                        if (val) sources.push(val);
                    });
                });
                return sources;
            }""")
            sources.update(attrs)
        except Exception as e:
            log.debug(f"data属性提取失败: {e}")

        try:
            scripts = await page.eval_on_selector_all(
                'script:not([src])', 
                'els => els.map(e => e.textContent || "")'
            )
            sources.update(scripts)
        except Exception as e:
            log.debug(f"script标签提取失败: {e}")

        try:
            styles = await page.eval_on_selector_all(
                'style', 
                'els => els.map(e => e.textContent || "")'
            )
            sources.update(styles)
        except Exception as e:
            log.debug(f"style标签提取失败: {e}")

        try:
            textareas = await page.eval_on_selector_all(
                'textarea', 
                'els => els.map(e => e.value || e.textContent || "")'
            )
            sources.update(textareas)
        except Exception as e:
            log.debug(f"textarea提取失败: {e}")

        return sources

    async def _extract_from_iframe(page: Page):
        try:
            iframes = await page.query_selector_all('iframe')
            for iframe in iframes[:3]:
                try:
                    frame = await iframe.content_frame()
                    if frame:
                        for item in _extract_from_text(await frame.content()):
                            _add(item)
                except Exception:
                    pass
        except Exception:
            pass

    async def _click_dynamic_buttons():
        button_selectors = [
            'button:has-text("加载更多")', 
            'button:has-text("Load More")',
            'button:has-text("更多")',
            'button:has-text("展开")',
            'button:has-text("展开全部")',
            '.pagination a',
            '.load-more',
            '.more-btn',
            '[class*="load"]',
            '[class*="more"]',
        ]
        
        for sel in button_selectors:
            try:
                buttons = await page.query_selector_all(sel)
                for btn in buttons[:2]:
                    try:
                        await btn.scroll_into_view_if_needed()
                        await btn.click(timeout=1500)
                        await page.wait_for_load_state("domcontentloaded", timeout=3000)
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
            except Exception:
                pass

    async def _scroll_and_wait():
        try:
            await page.evaluate("""async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        const scrollTop = document.documentElement.scrollTop || document.body.scrollTop;
                        const clientHeight = document.documentElement.clientHeight;
                        
                        window.scrollBy(0, clientHeight);
                        totalHeight += clientHeight;
                        
                        if (totalHeight >= scrollHeight || totalHeight > 10000) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 150);
                    setTimeout(() => { clearInterval(timer); resolve(); }, 6000);
                });
            }""")
            await asyncio.sleep(0.5)
        except Exception as e:
            log.debug(f"滚动失败: {e}")

    async def _wait_for_network():
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=2000)
            except Exception:
                pass

    sources = await _extract_text_sources()
    for text in sources:
        for item in _extract_from_text(text):
            _add(item)

    await _extract_from_iframe(page)

    await _click_dynamic_buttons()
    sources = await _extract_text_sources()
    for text in sources:
        for item in _extract_from_text(text):
            _add(item)

    await _scroll_and_wait()
    sources = await _extract_text_sources()
    for text in sources:
        for item in _extract_from_text(text):
            _add(item)

    await _wait_for_network()
    sources = await _extract_text_sources()
    for text in sources:
        for item in _extract_from_text(text):
            _add(item)

    return _deduplicate_magnets(results)


def _get_same_domain_links(html: str, base_url: str, max_links: int = 15) -> List[str]:
    base    = urlparse(base_url)
    pattern = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
    seen, links = set(), []
    
    skip_patterns = (
        r'\.(jpg|jpeg|png|gif|svg|css|js|ico|woff2?|ttf|eot|webp|mp3|mp4|avi|mkv|mov|pdf|zip|rar|7z)$',
        r'(login|register|signup|signin|password|reset)',
    )
    skip_re = re.compile(skip_patterns, re.IGNORECASE)
    
    for href in pattern.findall(html):
        full   = urljoin(base_url, href)
        parsed = urlparse(full)
        
        if (
            parsed.netloc == base.netloc
            and parsed.scheme in ("http", "https")
            and full not in seen
            and not skip_re.search(full)
        ):
            seen.add(full)
            links.append(full)
            if len(links) >= max_links:
                break
    
    return links


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


class MagnetCrawler:

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
        self._playwright = None
        self._browser    = None
        self._metrics    = None
        self._global_seen: Set[str] = set()

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser    = await self._playwright.chromium.launch(
            headless = self._config.headless,
            args     = [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--lang=zh-CN,zh",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        log.info("Playwright 浏览器已启动")

    async def stop(self):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._playwright:
                await self._playwright.stop()
        log.info("Playwright 浏览器已关闭")

    async def crawl(self, url: str, depth: int = 1) -> AsyncGenerator[dict, None]:
        if not self._browser:
            await self.start()

        self._metrics = CrawlMetrics()
        self._global_seen.clear()

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
            ignore_https_errors = True,
        )

        await context.set_extra_http_headers({
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        async def block_media(route):
            if route.request.resource_type in ("image", "media", "font", "websocket"):
                try:
                    await route.abort()
                except Exception:
                    pass
            else:
                await route.continue_()

        await context.route("**/*", block_media)

        visited:   Set[str] = set()

        try:
            async def _crawl_single(target_url: str, current_depth: int, retry_count: int = 0):
                if target_url in visited:
                    return
                visited.add(target_url)
                self._metrics.pages_crawled += 1

                page = await context.new_page()
                page.set_default_timeout(self._config.timeout * 1000)
                page.set_default_navigation_timeout(self._config.timeout * 1000)
                
                try:
                    stealth_config = stealth.Stealth()
                    await stealth_config.apply_stealth_async(page)
                    yield {"type": "progress", "msg": "正在爬取...", "url": target_url, "depth": current_depth}

                    try:
                        response = await page.goto(
                            target_url,
                            wait_until = "domcontentloaded",
                            timeout    = self._config.timeout * 1000,
                        )
                        
                        if response and response.status >= 400:
                            yield {"type": "error", "msg": f"HTTP {response.status}", "url": target_url}
                            return
                    except Exception as nav_error:
                        if retry_count < 2:
                            self._metrics.retries += 1
                            log.info(f"页面加载失败，重试 {retry_count + 1}: {target_url}")
                            await asyncio.sleep(2 ** retry_count)
                            async for msg in _crawl_single(target_url, current_depth, retry_count + 1):
                                yield msg
                            return
                        else:
                            yield {"type": "error", "msg": f"导航失败: {nav_error}", "url": target_url}
                            self._metrics.errors += 1
                            return

                    await asyncio.sleep(1.5)

                    items = await _extract_from_page(page, target_url)
                    new_count = 0
                    for item in items:
                        if item.hash not in self._global_seen:
                            self._global_seen.add(item.hash)
                            new_count += 1
                            self._metrics.magnets_found += 1
                            yield {"type": "found", "item": item.model_dump()}

                    yield {"type": "progress", "msg": f"发现 {new_count} 个新磁力 (累计 {len(items)})", 
                           "url": target_url, "metrics": self._metrics.as_dict()}

                    if current_depth < depth:
                        html      = await page.content()
                        sub_links = _get_same_domain_links(html, target_url)
                        for link in sub_links:
                            async for msg in _crawl_single(link, current_depth + 1):
                                yield msg

                except Exception as e:
                    yield {"type": "error", "msg": str(e), "url": target_url}
                    self._metrics.errors += 1
                    log.error(f"页面处理异常 [{target_url}]: {e}")
                finally:
                    await page.close()

            async for msg in _crawl_single(url, 1):
                yield msg

            yield {"type": "done", "total": self._metrics.magnets_found, 
                   "url": url, "metrics": self._metrics.as_dict()}

        finally:
            await context.close()

