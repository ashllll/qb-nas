"""
SiteAuth — 站点 Cookie 注入与自动登录支持。

从 .env SITE_COOKIES 读取站点 cookie，注入到 Scrapling 浏览器会话。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

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
