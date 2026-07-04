import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.bus import NullBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.magnet_parser import extract_from_text
from magnet_harvester.services.clipboard_monitor import ClipboardMonitor
from magnet_harvester.store import FakeStore


def test_clipboard_no_longer_uses_local_magnet_regex():
    """剪贴板模块不应再暴露内部 _MAGNET_RE 正则，应复用共享解析器。"""
    from magnet_harvester.services import clipboard_monitor

    assert not hasattr(clipboard_monitor, "_MAGNET_RE"), "clipboard_monitor 不应再暴露 _MAGNET_RE"


def test_clipboard_accepts_non_2160p_magnet():
    store = FakeStore()
    monitor = ClipboardMonitor(
        bus=NullBus(),
        store=store,
        classifier=LocalClassifier(),
        pipeline=None,
    )
    magnet = (
        "magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&dn=Example.Movie.1080p.WEB-DL"
    )
    items = extract_from_text(magnet)
    assert len(items) == 1

    monitor._running = True  # 模拟监控运行中，_handle_item 需要此标志
    asyncio.run(monitor._handle_item(items[0]))

    item = store.get("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert item is not None
    assert item.source_url == "clipboard://"
    assert item.name == "Example.Movie.1080p.WEB-DL"


def test_clipboard_accepts_base64_encoded_magnet():
    store = FakeStore()
    monitor = ClipboardMonitor(
        bus=NullBus(),
        store=store,
        classifier=LocalClassifier(),
        pipeline=None,
    )
    magnet = (
        "magnet:?xt=urn:btih:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        "&dn=Base64.Clipboard.1080p.Movie"
    )
    encoded = base64.b64encode(magnet.encode()).decode()
    content = f" hidden link: {encoded} "
    items = extract_from_text(content)
    assert len(items) == 1, f"应从 Base64 中解析出 1 个磁力，实际 {len(items)}"

    monitor._running = True  # 模拟监控运行中，_handle_item 需要此标志
    asyncio.run(monitor._handle_item(items[0]))

    item = store.get("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
    assert item is not None
    assert item.source_url == "clipboard://"
    assert item.name == "Base64.Clipboard.1080p.Movie"


def test_clipboard_accepts_html_escaped_and_quoted_magnet():
    store = FakeStore()
    monitor = ClipboardMonitor(
        bus=NullBus(),
        store=store,
        classifier=LocalClassifier(),
        pipeline=None,
    )
    content = (
        '<a href="magnet:?xt=urn:btih:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC'
        '&amp;dn=Quoted.Clipboard.720p.WEB-DL">link</a>'
    )
    items = extract_from_text(content)
    assert len(items) == 1

    monitor._running = True  # 模拟监控运行中，_handle_item 需要此标志
    asyncio.run(monitor._handle_item(items[0]))

    item = store.get("CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC")
    assert item is not None
    assert item.name == "Quoted.Clipboard.720p.WEB-DL"


def test_processed_content_fifo_eviction_not_full_clear():
    """_processed_content 达到上限时应 FIFO 逐出一半，不应全部清零。"""
    from unittest.mock import patch, MagicMock

    store = FakeStore()
    bus = NullBus()
    monitor = ClipboardMonitor(
        bus=bus,
        store=store,
        classifier=LocalClassifier(),
        pipeline=None,
        poll_interval=0.01,
    )

    # 填充到上限
    for i in range(10000):
        monitor._processed_content.add(f"content-{i}")

    assert len(monitor._processed_content) == 10000

    # 记录旧条目样本
    old_sample = {f"content-{i}" for i in range(0, 10000, 100)}

    # 模拟 pyperclip.paste 返回新内容，然后返回空触发退出
    paste_values = ["brand-new-clipboard-text"]

    def mock_paste():
        if paste_values:
            return paste_values.pop(0)
        return ""

    async def run_one_cycle():
        monitor._running = True
        monitor._stop_event.clear()
        with patch(
            "magnet_harvester.services.clipboard_monitor.pyperclip.paste",
            side_effect=mock_paste,
        ):
            # 执行 _run 的一轮迭代（直接调用 _run 并快速停止）
            task = asyncio.create_task(monitor._run())
            await asyncio.sleep(0.05)
            monitor._stop_event.set()
            monitor._running = False
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.CancelledError:
                pass

    asyncio.run(run_one_cycle())

    # 逐出后旧条目应部分保留（FIFO），不应全部清零
    surviving = sum(1 for s in old_sample if s in monitor._processed_content)
    total = len(monitor._processed_content)
    assert surviving > 0, f"逐出后应保留部分旧条目，实际保留 {surviving} 个（总数 {total}）"
    assert total > 100, f"逐出后大小应保留约一半，实际 {total}"


if __name__ == "__main__":
    test_clipboard_no_longer_uses_local_magnet_regex()
    test_clipboard_accepts_non_2160p_magnet()
    test_clipboard_accepts_base64_encoded_magnet()
    test_clipboard_accepts_html_escaped_and_quoted_magnet()
    print("=== clipboard monitor tests passed! ===")
