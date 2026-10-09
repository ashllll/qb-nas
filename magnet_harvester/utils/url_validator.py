"""Crawl target admission and SSRF prevention."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse


class URLValidationError(ValueError):
    """Raised when a URL fails Crawl target admission."""


Resolver = Callable[[str, int], Awaitable[list[str]]]


MAX_CRAWL_URL_LENGTH = 8192

# mihomo (Clash Meta) / Clash fake-IP 模式使用 198.18.0.0/15（RFC 2544 基准测试范围）
# 代理端本地 DNS 将域名解析为该网段地址，主机真实 IP（如 Cloudflare）为公网地址
FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _is_unsafe_address(value: str, allow_fake_ip: bool = False) -> bool:
    ip = ipaddress.ip_address(value)
    mapped_ipv4 = getattr(ip, "ipv4_mapped", None)
    if mapped_ipv4 is not None:
        return _is_unsafe_address(str(mapped_ipv4), allow_fake_ip=allow_fake_ip)
    if allow_fake_ip and ip in FAKE_IP_NETWORK:
        return False
    return (
        not ip.is_global
        or ip.is_multicast
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    )


def _validate_hostname(hostname: str | None, allow_fake_ip: bool = False) -> None:
    if not hostname:
        raise URLValidationError("URL has no hostname")
    if hostname.lower() == "localhost":
        raise URLValidationError("URL resolves to a private address")
    try:
        # 字面 IP 不做 fake-IP 豁免（fake-IP 只对域名解析生效，字面直连不走代理池）
        literal_flag = False
        ipaddress.ip_address(hostname)
    except ValueError:
        literal_flag = allow_fake_ip
    try:
        if _is_unsafe_address(hostname, allow_fake_ip=literal_flag):
            raise URLValidationError("URL resolves to a private address")
    except URLValidationError:
        raise
    except ValueError:
        pass


def _validate_protocol(parsed) -> None:
    if parsed.scheme not in ("http", "https"):
        if not parsed.scheme:
            raise URLValidationError("URL must start with http:// or https://")
        raise URLValidationError(f"Unsupported protocol: {parsed.scheme}")


def validate_crawl_url(url: str, allow_fake_ip: bool = False) -> bool:
    """Validate the literal URL shape before network resolution."""
    if not url or not url.strip():
        raise URLValidationError("URL is empty")
    candidate = url.strip()
    if len(candidate) > MAX_CRAWL_URL_LENGTH:
        raise URLValidationError("URL is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        raise URLValidationError("URL contains control characters")
    try:
        parsed = urlparse(candidate)
    except ValueError as exc:
        raise URLValidationError("URL is invalid") from exc
    _validate_protocol(parsed)
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLValidationError("URL port is invalid") from exc
    if port is not None and port < 1:
        raise URLValidationError("URL port is invalid")
    if parsed.username is not None or parsed.password is not None or "\\" in parsed.netloc:
        raise URLValidationError("URL contains invalid characters (@ or \\)")
    _validate_hostname(parsed.hostname, allow_fake_ip=allow_fake_ip)
    return True


async def _resolve_host(hostname: str, port: int, timeout: float = 5.0) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await asyncio.wait_for(
        loop.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        ),
        timeout=timeout,
    )
    return list({record[4][0] for record in records})


class CrawlTargetAdmission:
    """Admits initial and discovered Crawl targets."""

    def __init__(
        self,
        resolver: Resolver | None = None,
        allow_fake_ip: bool = False,
    ):
        self._resolver = resolver or _resolve_host
        self._allow_fake_ip = allow_fake_ip

    async def admit(self, url: str) -> str:
        candidate = url.strip()
        validate_crawl_url(candidate, allow_fake_ip=self._allow_fake_ip)
        parsed = urlparse(candidate)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = await self._resolver(parsed.hostname or "", port)
        except OSError as exc:
            raise URLValidationError(f"URL hostname cannot be resolved: {parsed.hostname}") from exc
        if not addresses:
            raise URLValidationError(f"URL hostname cannot be resolved: {parsed.hostname}")
        if any(
            _is_unsafe_address(address, allow_fake_ip=self._allow_fake_ip) for address in addresses
        ):
            raise URLValidationError("URL resolves to a private address")
        return candidate
