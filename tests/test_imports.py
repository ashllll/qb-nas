#!/usr/bin/env python3
"""测试脚本 - 验证所有模块是否可以正确导入"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

env_test = Path(__file__).parent.parent / '.env.test'
os.environ['DOTENV'] = str(env_test)

try:
    print("=== 测试模块导入 ===")

    print("\n1. 测试 config 模块...")
    from magnet_harvester.config import settings
    print(f"   OK - Config 加载成功")
    print(f"   - QBIT_HOST: {settings.QBIT_HOST}")

    print("\n2. 测试 models 模块...")
    from magnet_harvester.models import MagnetItem, TaskStatus, CrawlRequest
    item = MagnetItem(hash="ABC123", name="测试", magnet="magnet:?xt=urn:btih:ABC123")
    print(f"   OK - Models 加载成功 - 测试项目: {item.name}")

    print("\n3. 测试 magnet_parser 模块...")
    from magnet_harvester.magnet_parser import parse_magnet, extract_from_text
    parsed = parse_magnet("magnet:?xt=urn:btih:AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE")
    print(f"   OK - MagnetParser 加载成功")

    print("\n4. 测试 crawler 模块 (crawl4ai)...")
    from magnet_harvester.crawler import MagnetCrawler, CrawlMetrics
    metrics = CrawlMetrics()
    print(f"   OK - Crawler 加载成功")

    print("\n5. 测试 classifier 模块...")
    from magnet_harvester.classifier import LocalClassifier
    classifier = LocalClassifier()
    result = classifier.classify_one("Test.Movie.2024.1080p")
    print(f"   OK - LocalClassifier 加载成功 - 分类测试: {result['category']}")

    print("\n6. 测试 store 模块...")
    from magnet_harvester.store import InMemoryItemStore, FakeStore, ItemStore, StoreStats
    store = InMemoryItemStore()
    store.add(MagnetItem(hash="BEEF456", name="测试", magnet="magnet:?xt=urn:btih:BEEF456"))
    print(f"   OK - ItemStore 加载成功 - 条目数: {store.count}")

    print("\n7. 测试 bus 模块...")
    from magnet_harvester.bus import MessageBus, Event, EventType
    bus = MessageBus()
    print(f"   OK - MessageBus 加载成功 - 事件类型: {len(EventType)}")

    print("\n8. 测试 pipeline 模块...")
    from magnet_harvester.pipeline import HarvestPipeline, CrawlPhase, ClassifyPhase, DownloadPhase
    print(f"   OK - Pipeline 加载成功")

    print("\n9. 测试 studio_recognizer 模块...")
    from magnet_harvester.studio_recognizer import StudioRecognizer
    r = StudioRecognizer()
    result = r.recognize("SexArt.26.02.01.XXX.2160p")
    print(f"   OK - StudioRecognizer 加载成功 - SexArt: {result is not None}")

    print("\n10. 测试 qbit_client 模块...")
    from magnet_harvester.qbit_client import QBittorrentClient
    qbit = QBittorrentClient(config=settings.qbit)
    print(f"   OK - QBittorrentClient 加载成功")

    print("\n11. 测试 errors 模块...")
    from magnet_harvester.errors import error_handler, ErrorCategory, ErrorSeverity
    print(f"   OK - Errors 加载成功")

    print("\n12. 测试 main 模块...")
    from magnet_harvester.main import app, stats
    print(f"   OK - Main 应用加载成功 ({len(app.routes)} routes)")

    print("\n" + "="*50)
    print("所有模块导入成功!")
    print("="*50)
    sys.exit(0)

except Exception as e:
    print(f"\n导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
