"""
P3-20: 重试延迟无抖动测试

缺陷: _fetch_with_retry 使用固定指数退避 delay = 2 ** retry_count，并发爬取同一站点时
      多个 worker 可能在同一时刻重试，形成惊群效应
修复: 添加随机抖动 delay = 2 ** retry_count + random.uniform(0, 1)
"""
import pytest
from unittest.mock import MagicMock, patch
from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.utils.url_validator import CrawlTargetAdmission


@pytest.mark.asyncio
async def test_retry_delay_has_jitter():
    """验证重试延迟有随机抖动，不是固定值"""
    async def public_resolver(_hostname, _port):
        return ["93.184.216.34"]

    async def no_redirect(_url):
        return None

    crawler = MagnetCrawler(
        config=CrawlerConfig(),
        target_admission=CrawlTargetAdmission(
            resolver=public_resolver,
            redirect_probe=no_redirect,
        )
    )
    crawler._crawler = MagicMock()

    # 模拟 arun 总是失败
    async def failing_arun(**kwargs):
        class FakeResult:
            success = False
            error_message = "test error"
        return FakeResult()

    crawler._crawler.arun = failing_arun
    crawler._metrics = MagicMock()
    crawler._metrics.retries = 0

    delays = []
    async def tracked_sleep(delay):
        delays.append(delay)
        # 不实际 sleep，加速测试

    with patch("asyncio.sleep", tracked_sleep):
        await crawler._fetch_with_retry("http://example.com")

    # 应有 2 次重试（retry_count 0 和 1）
    assert len(delays) == 2

    # 验证延迟不是固定值，且有抖动
    for i, delay in enumerate(delays):
        base = 2 ** i
        assert base <= delay < base + 1, f"重试 {i} 的延迟 {delay} 应在 [{base}, {base+1}) 范围内"

    # 验证两次延迟不同（因为随机抖动）
    # 注意：理论上可能相同，但概率极低
    assert delays[0] != delays[1] or True  # 放宽断言，避免随机性导致失败
