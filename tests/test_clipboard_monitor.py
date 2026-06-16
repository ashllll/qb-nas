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

    assert not hasattr(clipboard_monitor, "_MAGNET_RE"), (
        "clipboard_monitor 不应再暴露 _MAGNET_RE"
    )


def test_clipboard_accepts_non_2160p_magnet():
    store = FakeStore()
    monitor = ClipboardMonitor(
        bus=NullBus(),
        store=store,
        classifier=LocalClassifier(),
        pipeline=None,
    )
    magnet = (
        "magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "&dn=Example.Movie.1080p.WEB-DL"
    )
    items = extract_from_text(magnet)
    assert len(items) == 1

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

    asyncio.run(monitor._handle_item(items[0]))

    item = store.get("CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC")
    assert item is not None
    assert item.name == "Quoted.Clipboard.720p.WEB-DL"


if __name__ == "__main__":
    test_clipboard_no_longer_uses_local_magnet_regex()
    test_clipboard_accepts_non_2160p_magnet()
    test_clipboard_accepts_base64_encoded_magnet()
    test_clipboard_accepts_html_escaped_and_quoted_magnet()
    print("=== clipboard monitor tests passed! ===")
