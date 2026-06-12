"""URL validation utilities — SSRF prevention."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse


class URLValidationError(ValueError):
    """Raised when a URL fails SSRF safety checks."""
    pass


# Characters that can be used for credential injection or path traversal
_INVALID_URL_CHARS_RE = re.compile(r'[@\\]')


def validate_crawl_url(url: str) -> bool:
    """Validate a URL is safe for server-side crawling.

    Rules:
    - Must be non-empty
    - Must use http:// or https://
    - Must not contain @ or backslash (credential injection)
    - Must not resolve to private/link-local IP ranges
    """
    if not url or not url.strip():
        raise URLValidationError("URL is empty")

    url = url.strip()

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        if not parsed.scheme:
            raise URLValidationError("URL must start with http:// or https://")
        raise URLValidationError(f"Unsupported protocol: {parsed.scheme}")

    if _INVALID_URL_CHARS_RE.search(url):
        raise URLValidationError("URL contains invalid characters (@ or \\)")

    hostname = parsed.hostname
    if not hostname:
        raise URLValidationError("URL has no hostname")

    # Check for localhost variants
    if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
        raise URLValidationError("URL resolves to a private address")

    # Check for private IP ranges
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise URLValidationError("URL resolves to a private address")
    except URLValidationError:
        raise
    except ValueError:
        # Not an IP address — it's a domain name, allow
        pass

    return True
