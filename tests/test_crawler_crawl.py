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

    # 用 Scrapling 官方文档测试（不含有磁力链接）
    test_url = "https://scrapling.readthedocs.io/en/latest/"
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


# ── 事件循环阻塞回归测试 ─────────────────────────


def test_handle_crawl_result_does_not_block_event_loop():
    """大页面解析不得阻塞事件循环（to_thread 化）。

    注：本测试依赖机器速度——阈值按保守值取，快机器上心跳更多，
    慢机器上只要 to_thread 生效（非完全阻塞）即可通过。
    """

    def _big_markdown():
        magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        base = f"<a href='{magnet}'>x</a>" * 50
        return base * 2000  # ~3MB

    class SlowResult:
        success = True
        url = "https://example.com/big"
        error_message = ""
        markdown = _big_markdown()  # 3MB+ 内容：extract_from_text 需要 ~100ms+ 同步解析
        cleaned_html = ""
        html = ""

    async def run():
        from magnet_harvester.crawler import MagnetCrawler

        # 显式固定 allowed_resolutions，避免依赖默认值变化
        crawler = MagnetCrawler(
            config=CrawlerConfig(headless=True, timeout=30, allowed_resolutions=("2160p", "4k"))
        )
        events = asyncio.Queue()
        heartbeat_ticks = []

        async def heartbeat():
            # 事件循环空闲时每 2ms 跳一次；解析若同步阻塞则期间无心跳
            while True:
                await asyncio.sleep(0.002)
                heartbeat_ticks.append(1)

        hb = asyncio.create_task(heartbeat())
        try:
            await crawler._handle_crawl_result(
                SlowResult(), "https://example.com/big", events, set()
            )
        finally:
            hb.cancel()
            await asyncio.gather(hb, return_exceptions=True)

        # 同步解析（阻塞 ~670ms）时心跳为 0；to_thread 后 GIL 间隙可跳动。
        # 阈值取保守值 ≥15（GIL 周期性饿死下仍远低于 2ms/次的理论值）。
        assert len(heartbeat_ticks) >= 15, (
            f"事件循环被阻塞，解析期间心跳仅 {len(heartbeat_ticks)} 次"
        )

    asyncio.run(run())
