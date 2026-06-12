"""Tests for URL validation to prevent SSRF attacks."""
from __future__ import annotations

import pytest

from magnet_harvester.utils.url_validator import (
    CrawlTargetAdmission,
    URLValidationError,
    validate_crawl_url,
)


class TestValidateCrawlUrl:
    """Red: test URL validation rules for SSRF prevention."""

    def test_accepts_valid_http_url(self):
        assert validate_crawl_url("http://example.com/page") is True

    def test_accepts_valid_https_url(self):
        assert validate_crawl_url("https://example.com/page") is True

    def test_rejects_file_protocol(self):
        with pytest.raises(URLValidationError, match="Unsupported protocol"):
            validate_crawl_url("file:///etc/passwd")

    def test_rejects_ftp_protocol(self):
        with pytest.raises(URLValidationError, match="Unsupported protocol"):
            validate_crawl_url("ftp://example.com/file")

    def test_rejects_localhost(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://localhost:8080")

    def test_rejects_127_0_0_1(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://127.0.0.1:8080")

    def test_rejects_192_168_x_x(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://192.168.1.100:8080")

    def test_rejects_10_0_0_0(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://10.0.0.1")

    def test_rejects_172_16_0_0(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://172.16.0.1")

    def test_rejects_169_254_link_local(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://169.254.1.1")

    def test_rejects_empty_url(self):
        with pytest.raises(URLValidationError, match="empty"):
            validate_crawl_url("")

    def test_rejects_whitespace_only_url(self):
        with pytest.raises(URLValidationError, match="empty"):
            validate_crawl_url("   ")

    def test_rejects_url_without_protocol(self):
        with pytest.raises(URLValidationError, match="must start with"):
            validate_crawl_url("example.com")

    def test_rejects_url_with_at_sign(self):
        with pytest.raises(URLValidationError, match="invalid characters"):
            validate_crawl_url("http://user:pass@example.com")

    def test_rejects_url_with_backslash(self):
        with pytest.raises(URLValidationError, match="invalid characters"):
            validate_crawl_url("http://example.com\\@evil.com")

    def test_accepts_public_ip(self):
        assert validate_crawl_url("http://8.8.8.8") is True

    def test_accepts_public_ip_https(self):
        assert validate_crawl_url("https://1.1.1.1") is True


@pytest.mark.asyncio
async def test_admission_rejects_hostname_resolving_to_private_address():
    async def private_resolver(_hostname, _port):
        return ["10.0.0.5"]

    admission = CrawlTargetAdmission(resolver=private_resolver)

    with pytest.raises(URLValidationError, match="private"):
        await admission.admit("https://public-looking.example")


@pytest.mark.asyncio
async def test_admission_rejects_redirect_to_private_address():
    async def resolver(hostname, _port):
        return ["10.0.0.5"] if hostname == "internal.example" else ["93.184.216.34"]

    async def redirect_probe(url):
        if url == "https://public.example":
            return "http://internal.example/admin"
        return None

    admission = CrawlTargetAdmission(
        resolver=resolver,
        redirect_probe=redirect_probe,
    )

    with pytest.raises(URLValidationError, match="private"):
        await admission.admit_redirect_chain("https://public.example")
