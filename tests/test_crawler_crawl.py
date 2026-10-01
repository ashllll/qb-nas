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
    """大页面解析必须卸载到工作线程，不得占用事件循环线程。

    断言"解析发生在非事件循环线程"这一确定性事实，取代此前基于心跳计数的
    时序阈值：to_thread 之后解析是 CPU 密集的正则工作，会与事件循环争抢 GIL，
    心跳次数只反映 GIL 调度，无法区分"已卸载但抢占激烈"与"完全未卸载"，
    因而在快机器上会误报失败。
    """
    import threading

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
        loop_thread = threading.get_ident()
        seen_threads = []

        original_extract = crawler._extract_page_items

        def spy_extract(result, *, source_url):
            seen_threads.append(threading.get_ident())
            return original_extract(result, source_url=source_url)

        crawler._extract_page_items = spy_extract

        await crawler._handle_crawl_result(SlowResult(), "https://example.com/big", events, set())

        assert seen_threads, "解析函数未被调用，测试未覆盖目标路径"
        assert all(tid != loop_thread for tid in seen_threads), (
            f"解析在事件循环线程上同步执行（loop={loop_thread}, 实际={seen_threads}），"
            "必须经 asyncio.to_thread 卸载"
        )

    asyncio.run(run())
