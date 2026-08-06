"""Tests for URL validation to prevent SSRF attacks."""

from __future__ import annotations

import httpx
import pytest

from magnet_harvester.utils.url_validator import (
    CrawlTargetAdmission,
    REDIRECT_PROBE_TIMEOUT_SEC,
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

    def test_rejects_ipv4_mapped_private_ipv6(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://[::ffff:192.168.1.10]/admin")

    def test_rejects_non_global_benchmark_network(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://198.18.0.1/admin")

    def test_rejects_deprecated_ipv6_site_local_network(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://[fec0::1]/admin")

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

    def test_accepts_at_sign_in_query_value(self):
        assert validate_crawl_url("https://example.com/search?q=user@example.com") is True

    def test_rejects_url_with_backslash(self):
        with pytest.raises(URLValidationError, match="invalid characters"):
            validate_crawl_url("http://example.com\\@evil.com")

    def test_accepts_public_ip(self):
        assert validate_crawl_url("http://8.8.8.8") is True

    def test_accepts_public_ip_https(self):
        assert validate_crawl_url("https://1.1.1.1") is True

    def test_rejects_port_out_of_range_as_validation_error(self):
        with pytest.raises(URLValidationError, match="port"):
            validate_crawl_url("https://example.com:99999")

    def test_rejects_zero_port(self):
        with pytest.raises(URLValidationError, match="port"):
            validate_crawl_url("https://example.com:0")

    def test_rejects_malformed_ipv6_as_validation_error(self):
        with pytest.raises(URLValidationError, match="invalid"):
            validate_crawl_url("http://[::1")

    def test_rejects_embedded_control_characters(self):
        with pytest.raises(URLValidationError, match="control"):
            validate_crawl_url("https://exa\nmple.com")

    def test_rejects_excessively_long_url(self):
        with pytest.raises(URLValidationError, match="too long"):
            validate_crawl_url("https://example.com/" + "a" * 9000)


def test_redirect_probe_timeout_is_short_for_crawl_speed():
    assert REDIRECT_PROBE_TIMEOUT_SEC == 2.0


@pytest.mark.asyncio
async def test_admission_rejects_hostname_resolving_to_private_address():
    async def private_resolver(_hostname, _port):
        return ["10.0.0.5"]

    admission = CrawlTargetAdmission(resolver=private_resolver)

    with pytest.raises(URLValidationError, match="private"):
        await admission.admit("https://public-looking.example")


@pytest.mark.asyncio
async def test_admission_wraps_dns_timeout_as_validation_error():
    async def timeout_resolver(_hostname, _port):
        raise TimeoutError("dns timed out")

    admission = CrawlTargetAdmission(resolver=timeout_resolver)

    with pytest.raises(URLValidationError, match="cannot be resolved"):
        await admission.admit("https://slow-dns.example")


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


@pytest.mark.asyncio
async def test_redirect_probe_failure_is_fail_closed():
    async def resolver(_hostname, _port):
        return ["93.184.216.34"]

    async def failing_probe(_url):
        raise httpx.TimeoutException("probe timed out")

    admission = CrawlTargetAdmission(resolver=resolver, redirect_probe=failing_probe)

    with pytest.raises(URLValidationError, match="redirect"):
        await admission.admit_redirect_chain("https://public.example")


@pytest.mark.asyncio
async def test_redirect_chain_allows_exact_configured_redirect_limit():
    async def resolver(_hostname, _port):
        return ["93.184.216.34"]

    async def redirect_probe(url):
        if url == "https://public.example":
            return "https://public.example/final"
        return None

    admission = CrawlTargetAdmission(
        resolver=resolver,
        redirect_probe=redirect_probe,
        max_redirects=1,
    )

    assert (
        await admission.admit_redirect_chain("https://public.example")
        == "https://public.example/final"
    )


# ── allow_fake_ip 参数测试 (mihomo/Clash fake-IP 198.18.0.0/15 SSRF 豁免) ──


class TestValidateCrawlUrlWithFakeIp:
    """allow_fake_ip 标志的控制测试。"""

    def test_rejects_fake_ip_by_default(self):
        """默认 (allow_fake_ip=False) 仍阻止 198.18.0.0/15。"""
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://198.18.0.1/admin")

    def test_accepts_fake_ip_when_flag_enabled(self):
        """字面 IP 不再豁免：allow_fake_ip=True 也拒绝 198.18.0.0/15 字面地址。"""
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://198.18.2.102/torrents", allow_fake_ip=True)

    def test_accepts_198_19_x_when_flag_enabled(self):
        """198.19.x 属于 /15 范围，字面地址同样拒绝。"""
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://198.19.255.255/test", allow_fake_ip=True)

    def test_still_rejects_192_168_with_fake_ip_flag(self):
        """allow_fake_ip=True 不应绕过 RFC 1918 私有地址。"""
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://192.168.1.100:8080", allow_fake_ip=True)

    def test_still_rejects_10_x_with_fake_ip_flag(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://10.0.0.1", allow_fake_ip=True)

    def test_still_rejects_localhost_with_fake_ip_flag(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://localhost:8080", allow_fake_ip=True)

    def test_still_rejects_127_0_0_1_with_fake_ip_flag(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://127.0.0.1:8080", allow_fake_ip=True)

    def test_still_rejects_169_254_with_fake_ip_flag(self):
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://169.254.1.1", allow_fake_ip=True)

    def test_still_rejects_ipv4_mapped_private_with_fake_ip_flag(self):
        """IPv4-mapped IPv6 私有地址标志不受影响。"""
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://[::ffff:192.168.1.10]/admin", allow_fake_ip=True)

    def test_still_rejects_ipv4_mapped_fake_ip_without_flag(self):
        """默认阻止 IPv4-mapped fake-IP。"""
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://[::ffff:198.18.2.102]/admin")

    def test_accepts_ipv4_mapped_fake_ip_with_flag(self):
        """IPv4-mapped fake-IP 也是字面地址，allow_fake_ip=True 时同样拒绝。"""
        with pytest.raises(URLValidationError, match="private"):
            validate_crawl_url("http://[::ffff:198.18.2.102]/admin", allow_fake_ip=True)


@pytest.mark.asyncio
class TestCrawlTargetAdmissionWithFakeIp:
    """CrawlTargetAdmission 的 allow_fake_ip 集成测试。"""

    async def test_default_admission_rejects_fake_ip_resolver(self):
        """默认 (allow_fake_ip=False) 拒绝 DNS 解析到 198.18.x.x。"""

        async def fake_resolver(_hostname, _port):
            return ["198.18.2.102"]

        admission = CrawlTargetAdmission(resolver=fake_resolver)
        with pytest.raises(URLValidationError, match="private"):
            await admission.admit("https://xxxclub.to/torrents/search")

    async def test_admission_with_flag_allows_fake_ip_resolver(self):
        """allow_fake_ip=True 放行 DNS 解析到 198.18.x.x。"""

        async def fake_resolver(_hostname, _port):
            return ["198.18.2.102"]

        admission = CrawlTargetAdmission(resolver=fake_resolver, allow_fake_ip=True)
        result = await admission.admit("https://xxxclub.to/torrents/search")
        assert result == "https://xxxclub.to/torrents/search"

    async def test_admission_with_flag_allows_mixed_resolver(self):
        """Cloudflare 真实 IP + fake-IP 混合仍应放行。"""

        async def fake_resolver(_hostname, _port):
            return ["104.21.11.183", "198.18.2.102"]

        admission = CrawlTargetAdmission(resolver=fake_resolver, allow_fake_ip=True)
        result = await admission.admit("https://xxxclub.to/torrents/search")
        assert result == "https://xxxclub.to/torrents/search"

    async def test_admission_with_flag_still_rejects_private_resolver(self):
        """allow_fake_ip=True 不放行 RFC 1918 私有地址。"""

        async def fake_resolver(_hostname, _port):
            return ["192.168.1.1"]

        admission = CrawlTargetAdmission(resolver=fake_resolver, allow_fake_ip=True)
        with pytest.raises(URLValidationError, match="private"):
            await admission.admit("https://internal.example")
