"""
集成测试：MagnetCrawler (crawl4ai 适配器) 生命周期
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.config import CrawlerConfig


@pytest.mark.asyncio
async def test_crawler_start_stop():
    """验证 crawler 使用 crawl4ai 能正常启动和关闭"""
    config = CrawlerConfig(headless=True, timeout=10)
    crawler = MagnetCrawler(config=config)
    
    try:
        # 启动
        await crawler.start()
        # 确认已启动（没有异常即可）
        assert crawler._crawler is not None, "crawl4ai AsyncWebCrawler 应已创建"
    finally:
        # 关闭
        await crawler.stop()
    
    assert crawler._crawler is None, "关闭后 _crawler 应为 None"


@pytest.mark.asyncio
async def test_crawler_context_manager():
    """验证 async with 用法（通过 start/stop 模拟）"""
    config = CrawlerConfig(headless=True, timeout=10)
    crawler = MagnetCrawler(config=config)
    
    # 启动前不能 crawl
    assert crawler._crawler is None
    
    await crawler.start()
    try:
        assert crawler._crawler is not None
    finally:
        await crawler.stop()
    
    assert crawler._crawler is None


if __name__ == "__main__":
    import asyncio
    
    async def run():
        config = CrawlerConfig(headless=True, timeout=10)
        crawler = MagnetCrawler(config=config)
        print("正在启动 crawl4ai 爬虫...")
        await crawler.start()
        print("爬虫已启动 ✓")
        print(f"  AsyncWebCrawler: {crawler._crawler}")
        await crawler.stop()
        print("爬虫已关闭 ✓")
        print("生命周期测试通过!")
    
    asyncio.run(run())
