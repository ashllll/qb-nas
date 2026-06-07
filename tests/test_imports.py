#!/usr/bin/env python3
"""测试脚本 - 验证所有模块是否可以正确导入"""
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path 中（支持从 tests/ 目录运行）
sys.path.insert(0, str(Path(__file__).parent.parent))

# 设置测试环境变量
env_test = Path(__file__).parent.parent / '.env.test'
os.environ['DOTENV'] = str(env_test)

try:
    print("=== 测试模块导入 ===")
    
    print("\n1. 测试 config 模块...")
    from magnet_harvester.config import settings
    print(f"   OK - Config 加载成功")
    print(f"   - QBIT_HOST: {settings.QBIT_HOST}")
    print(f"   - 分类路径: {len(settings.CATEGORY_PATHS)} 个")
    
    print("\n2. 测试 models 模块...")
    from magnet_harvester.models import MagnetItem, TaskStatus, CrawlRequest
    item = MagnetItem(hash="ABC123", name="测试", magnet="magnet:?xt=urn:btih:ABC123")
    print(f"   OK - Models 加载成功 - 测试项目: {item.name}")
    
    print("\n3. 测试 errors 模块...")
    from magnet_harvester.errors import error_handler, ErrorCategory, ErrorSeverity
    error_handler.record(ErrorCategory.CRAWLER, ErrorSeverity.INFO, "测试错误")
    print(f"   OK - Errors 加载成功 - 错误数: {len(error_handler.get_recent_errors())}")
    
    print("\n4. 测试 classifier 模块...")
    from magnet_harvester.classifier.local_classifier import LocalClassifier
    classifier = LocalClassifier()
    result = classifier.classify_one("Test.Movie.2024.1080p")
    print(f"   OK - LocalClassifier 加载成功 - 分类测试: {result['category']}")
    
    print("\n5. 测试 qbit_client 模块...")
    from magnet_harvester.qbit_client import QBittorrentClient, QBittorrentStats
    qbit_client = QBittorrentClient()
    print(f"   OK - qBittorrent 客户端加载成功")
    
    print("\n6. 测试 magnet_parser 模块 (新)...")
    from magnet_harvester.magnet_parser import (
        MAGNET_RE, HASH_RE, parse_magnet, extract_from_text, try_decode_base64
    )
    parsed = parse_magnet("magnet:?xt=urn:btih:AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE")
    print(f"   OK - MagnetParser 加载成功 - 解析测试: {parsed is not None}")
    print(f"   - Base64 正则: {MAGNET_RE.pattern[:50]}...")

    print("\n7. 测试 crawler 模块 (crawl4ai 适配器)...")
    from magnet_harvester.crawler import MagnetCrawler, CrawlMetrics
    metrics = CrawlMetrics()
    print(f"   OK - Crawler 加载成功 - 指标支持: {metrics is not None}")

    print("\n8. 测试 store 模块 (新 ItemStore)...")
    from magnet_harvester.store import InMemoryItemStore, StoreStats
    store = InMemoryItemStore()
    from magnet_harvester.models import MagnetItem
    store.add(MagnetItem(hash="BEEF456", name="store测试", magnet="magnet:?xt=urn:btih:BEEF456"))
    print(f"   OK - ItemStore 加载成功 - 条目数: {store.count}")

    print("\n9. 测试 bus 模块 (新 MessageBus)...")
    from magnet_harvester.bus import MessageBus, Event, EventType
    bus = MessageBus()
    print(f"   OK - MessageBus 加载成功 - 事件类型: {len(EventType)}")

    print("\n10. 测试 pipeline 模块 (新 HarvestPipeline)...")
    from magnet_harvester.pipeline import HarvestPipeline
    print(f"   OK - Pipeline 模块加载成功")
    
    print("\n11. 测试 agent 模块...")
    from magnet_harvester.agent import MagnetAgent, AGENT_TOOLS
    print(f"   OK - Agent 加载成功 - 工具数: {len(AGENT_TOOLS)}")
    
    print("\n12. 测试 tts_client 模块...")
    from magnet_harvester.tts_client import MinimaxTTS
    tts_client = MinimaxTTS()
    print(f"   OK - TTS 客户端加载成功")
    
    print("\n13. 测试 main 模块...")
    from magnet_harvester.main import app, stats, SystemStats
    print(f"   OK - Main 应用加载成功")
    
    print("\n" + "="*50)
    print("所有模块导入成功!")
    print("="*50)
    
    sys.exit(0)
    
except Exception as e:
    print(f"\n导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
