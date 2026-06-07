"""
集成测试：MagnetCrawler.crawl() 生成器协议
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio

from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.config import CrawlerConfig


async def main():
    config = CrawlerConfig(headless=True, timeout=30)
    crawler = MagnetCrawler(config=config)

    print("=" * 50)
    print("测试 crawl() 生成器协议")
    print("=" * 50)

    # 用 crawl4ai 官方文档测试（不含有磁力链接）
    test_url = "https://docs.crawl4ai.com/"
    print(f"\n1. 爬取页面: {test_url}")
    print("-" * 40)

    msg_types_seen = set()

    async for msg in crawler.crawl(test_url, depth=1):
        msg_type = msg["type"]
        msg_types_seen.add(msg_type)

        if msg_type == "progress":
            print(f"   [进度] {msg.get('msg', '')}")
        elif msg_type == "found":
            print(f"   [发现] {msg['item']['hash'][:12]}... - {msg['item']['name']}")
        elif msg_type == "error":
            print(f"   [错误] {msg.get('msg', '')}")
        elif msg_type == "done":
            metrics = msg.get("metrics", {})
            print(f"   [完成] 共 {msg['total']} 个磁力链接")
            print(f"          爬取 {metrics.get('pages_crawled', 0)} 页")
            print(f"          耗时 {metrics.get('elapsed_sec', 0)} 秒")

    print(f"\n2. 生成的消息类型: {msg_types_seen}")
    assert "done" in msg_types_seen, "crawl() 必须产出 type=done 消息"
    assert "progress" in msg_types_seen, "crawl() 必须产出 type=progress 消息"
    print("crawl() 生成器协议测试通过!")


if __name__ == "__main__":
    asyncio.run(main())
