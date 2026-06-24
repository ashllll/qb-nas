"""Crawl target admission and SSRF prevention."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlparse

import httpx


class URLValidationError(ValueError):
    """Raised when a URL fails Crawl target admission."""


_INVALID_URL_CHARS_RE = re.compile(r"[@\\]")
Resolver = Callable[[str, int], Awaitable[list[str]]]
RedirectProbe = Callable[[str], Awaitable[str | None]]


_RFC1918_NETS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("100.64.0.0/10"),  # RFC 6598 CGNAT
    ipaddress.IPv6Network("fc00::/7"),
)
REDIRECT_PROBE_TIMEOUT_SEC = 2.0


def _is_unsafe_address(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    for net in _RFC1918_NETS:
        if ip in net:
            return True
    return False


def _validate_hostname(hostname: str | None) -> None:
    if not hostname:
        raise URLValidationError("URL has no hostname")
    if hostname.lower() == "localhost":
        raise URLValidationError("URL resolves to a private address")
    try:
        if _is_unsafe_address(hostname):
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


def validate_crawl_url(url: str) -> bool:
    """Validate the literal URL shape before network resolution."""
    if not url or not url.strip():
        raise URLValidationError("URL is empty")
    parsed = urlparse(url.strip())
    _validate_protocol(parsed)
    if _INVALID_URL_CHARS_RE.search(url):
        raise URLValidationError("URL contains invalid characters (@ or \\)")
    _validate_hostname(parsed.hostname)
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


async def _probe_redirect(url: str) -> str | None:
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=REDIRECT_PROBE_TIMEOUT_SEC,
    ) as client:
        response = await client.head(url)
    if response.is_redirect:
        location = response.headers.get("location")
        return urljoin(url, location) if location else None
    return None


class CrawlTargetAdmission:
    """Admits initial, discovered, and redirect Crawl targets."""

    def __init__(
        self,
        resolver: Resolver | None = None,
        redirect_probe: RedirectProbe | None = None,
        max_redirects: int = 5,
    ):
        self._resolver = resolver or _resolve_host
        self._redirect_probe = redirect_probe or _probe_redirect
        self._max_redirects = max_redirects

    async def admit(self, url: str) -> str:
        candidate = url.strip()
        validate_crawl_url(candidate)
        parsed = urlparse(candidate)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = await self._resolver(parsed.hostname or "", port)
        except OSError as exc:
            raise URLValidationError(f"URL hostname cannot be resolved: {parsed.hostname}") from exc
        if not addresses:
            raise URLValidationError(f"URL hostname cannot be resolved: {parsed.hostname}")
        if any(_is_unsafe_address(address) for address in addresses):
            raise URLValidationError("URL resolves to a private address")
        return candidate

    async def admit_redirect_chain(self, url: str) -> str:
        current = await self.admit(url)
        for _ in range(self._max_redirects):
            try:
                target = await self._redirect_probe(current)
            except httpx.HTTPError:
                return current
            if target is None:
                return current
            current = await self.admit(target)
        raise URLValidationError("URL redirects too many times")
