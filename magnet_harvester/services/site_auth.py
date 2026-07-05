"""
SiteAuth — 站点 Cookie 注入与自动登录支持。

从 .env SITE_COOKIES 读取站点 cookie，注入到 Scrapling 浏览器会话。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SiteAuth:
    """Builds browser cookies for authenticated crawl sessions."""

    site_cookies: dict[str, str]

    @classmethod
    def from_raw(cls, raw: str) -> "SiteAuth":
        return cls(parse_site_cookies(raw))

    def browser_cookies(self) -> list[dict]:
        cookies = build_browser_cookies(self.site_cookies)
        if cookies:
            log.info(
                "已加载 %s 个站点 cookie，覆盖 %s 个域名",
                len(cookies),
                len(self.site_cookies),
            )
        return cookies


def parse_site_cookies(raw: str) -> dict[str, str]:
    """解析 SITE_COOKIES JSON 配置。"""
    try:
        return json.loads(raw) if raw and raw.strip() else {}
    except json.JSONDecodeError:
        log.warning("SITE_COOKIES JSON 解析失败")
        return {}


def build_browser_cookies(site_cookies: dict[str, str]) -> list[dict]:
    """Build all configured site cookies in Playwright format."""
    cookies: list[dict] = []
    for domain, cookie_str in site_cookies.items():
        if not domain or not cookie_str:
            continue
        cookies.extend(_parse_cookie_string(cookie_str, domain))
    return cookies


def get_cookies_for_url(url: str, site_cookies: dict[str, str]) -> list[dict]:
    """返回匹配目标 URL 域名的 cookie 列表（Playwright 格式），精确匹配优先。"""
    if not site_cookies:
        return []

    try:
        domain = urlparse(url).hostname or ""
    except ValueError as e:
        log.debug("URL 解析失败 domain=%s: %s", url, e)
        return []
    except Exception as e:
        log.warning("get_cookies_for_url 非预期异常: %s", e)
        return []

    exact_cookies: list[dict] = []
    wildcard_cookies: list[dict] = []
    for site_domain, cookie_str in site_cookies.items():
        if not site_domain or not cookie_str:
            continue
        site_domain = site_domain.lstrip(".")
        # 域名匹配：精确匹配或子域名匹配
        if domain == site_domain:
            exact_cookies.extend(_parse_cookie_string(cookie_str, domain))
        elif domain.endswith("." + site_domain):
            wildcard_cookies.extend(_parse_cookie_string(cookie_str, domain))

    cookies = exact_cookies + wildcard_cookies
    if cookies:
        log.info(f"已注入 {len(cookies)} 个 cookie 到 {domain}")
    return cookies


def _parse_cookie_string(raw: str, domain: str) -> list[dict]:
    """将 Cookie 字符串解析为 Playwright 格式的 cookie 列表。"""
    result: list[dict] = []
    for item in raw.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        name, _, value = item.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        result.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
            }
        )
    return result
