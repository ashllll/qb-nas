import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.bus import NullBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.services.clipboard_monitor import ClipboardMonitor
from magnet_harvester.store import FakeStore


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

    asyncio.run(monitor._handle_magnet(magnet))

    item = store.get("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert item is not None
    assert item.source_url == "clipboard://"
    assert item.name == "Example.Movie.1080p.WEB-DL"


if __name__ == "__main__":
    test_clipboard_accepts_non_2160p_magnet()
    print("=== clipboard monitor tests passed! ===")
