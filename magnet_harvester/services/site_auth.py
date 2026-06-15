"""
SiteAuth — 站点 Cookie 注入与自动登录支持。

从 .env SITE_COOKIES 读取站点 cookie，注入到 crawl4ai 浏览器会话。
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def parse_site_cookies(raw: str) -> dict[str, str]:
    """解析 SITE_COOKIES JSON 配置。"""
    try:
        return json.loads(raw) if raw and raw.strip() else {}
    except json.JSONDecodeError:
        log.warning("SITE_COOKIES JSON 解析失败")
        return {}


def get_cookies_for_url(url: str, site_cookies: dict[str, str]) -> list[dict]:
    """返回匹配目标 URL 域名的 cookie 列表（Playwright 格式）。"""
    if not site_cookies:
        return []

    try:
        domain = urlparse(url).hostname or ""
    except Exception:
        return []

    for site_domain, cookie_str in site_cookies.items():
        if not site_domain or not cookie_str:
            continue
        # 域名匹配：精确匹配或子域名匹配
        if domain == site_domain or domain.endswith("." + site_domain):
            cookies = _parse_cookie_string(cookie_str, domain)
            if cookies:
                log.info(f"已注入 {len(cookies)} 个 cookie 到 {domain}（匹配 {site_domain}）")
            return cookies

    return []


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
        result.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
        })
    return result
