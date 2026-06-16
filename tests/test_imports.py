#!/usr/bin/env python3
"""验证主要模块可以正确导入和最小实例化。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

env_test = Path(__file__).parent.parent / ".env.test"
os.environ["DOTENV"] = str(env_test)


def test_core_modules_import_and_instantiate():
    from magnet_harvester.bus import Event, EventType, MessageBus
    from magnet_harvester.classifier import LocalClassifier
    from magnet_harvester.config import settings
    from magnet_harvester.crawler import CrawlMetrics, MagnetCrawler
    from magnet_harvester.errors import ErrorCategory, ErrorHandler, ErrorSeverity
    from magnet_harvester.magnet_parser import parse_magnet
    from magnet_harvester.models import CrawlRequest, MagnetItem, TaskStatus
    from magnet_harvester.pipeline import HarvestPipeline
    from magnet_harvester.crawler import CrawlPhase
    from magnet_harvester.pipeline import ClassifyPhase, DownloadPhase
    from magnet_harvester.qbit_client import QBittorrentClient
    from magnet_harvester.store import FakeStore, InMemoryItemStore, ItemStore, StoreStats
    from magnet_harvester.classifier.keyword_recognizer import KeywordCategoryRecognizer

    item = MagnetItem(hash="ABC123", name="测试", magnet="magnet:?xt=urn:btih:ABC123")
    assert item.status == TaskStatus.pending
    assert CrawlRequest(url="https://example.com", depth=9).depth == 3

    parsed = parse_magnet("magnet:?xt=urn:btih:AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE")
    assert parsed is not None

    assert CrawlMetrics().as_dict()["pages_crawled"] == 0
    assert MagnetCrawler is not None

    classifier = LocalClassifier()
    assert classifier.classify_one("Test.Movie.2024.1080p")["category"]

    store = InMemoryItemStore()
    store.add(MagnetItem(hash="BEEF456", name="测试", magnet="magnet:?xt=urn:btih:BEEF456"))
    assert store.count == 1
    assert FakeStore is InMemoryItemStore
    assert ItemStore is not None
    assert StoreStats().total == 0

    assert MessageBus is not None
    assert Event(EventType.ERROR, {"msg": "x"}).as_dict()["type"] == "error"

    assert HarvestPipeline is not None
    assert CrawlPhase is not None
    assert ClassifyPhase is not None
    assert DownloadPhase is not None

    recognizer = KeywordCategoryRecognizer()
    assert recognizer.recognize("Ubuntu.24.04.Desktop.iso") is not None

    assert QBittorrentClient(config=settings.qbit) is not None
    assert ErrorHandler() is not None
    assert ErrorCategory.CRAWLER.value == "crawler"
    assert ErrorSeverity.ERROR.value == "error"

    from magnet_harvester.main import app

    assert len(app.routes) > 0


if __name__ == "__main__":
    test_core_modules_import_and_instantiate()
    print("=== import tests passed! ===")
