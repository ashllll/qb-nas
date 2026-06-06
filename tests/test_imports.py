#!/usr/bin/env python3
"""测试脚本 - 验证所有模块是否可以正确导入"""
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path 中（支持从 tests/ 目录运行）
sys.path.insert(0, str(Path(__file__).parent.parent))

# 设置测试环境变量
os.environ['DOTENV'] = '/Users/llll/code/github/qb-nas/.env.test'

try:
    print("🔍 测试模块导入...")
    
    # 测试各个模块
    print("\n1. 测试 config 模块...")
    from magnet_harvester.config import settings
    print(f"   ✅ Config 加载成功")
    print(f"   - QBIT_HOST: {settings.QBIT_HOST}")
    print(f"   - 分类路径: {len(settings.CATEGORY_PATHS)} 个")
    
    print("\n2. 测试 models 模块...")
    from magnet_harvester.models import MagnetItem, TaskStatus, CrawlRequest
    item = MagnetItem(hash="ABC123", name="测试", magnet="magnet:?xt=urn:btih:ABC123")
    print(f"   ✅ Models 加载成功 - 测试项目: {item.name}")
    
    print("\n3. 测试 errors 模块...")
    from magnet_harvester.errors import error_handler, ErrorCategory, ErrorSeverity
    error_handler.record(ErrorCategory.CRAWLER, ErrorSeverity.INFO, "测试错误")
    print(f"   ✅ Errors 加载成功 - 错误数: {len(error_handler.get_recent_errors())}")
    
    print("\n4. 测试 classifier 模块...")
    from magnet_harvester.classifier import MiniMaxClassifier, ClassificationCache, BatchOptimizer
    cache = ClassificationCache()
    optimizer = BatchOptimizer()
    classifier = MiniMaxClassifier()
    print(f"   ✅ Classifier 加载成功 - 缓存支持: {cache is not None}")
    
    print("\n5. 测试 qbit_client 模块...")
    from magnet_harvester.qbit_client import QBittorrentClient, QBittorrentStats
    qbit_client = QBittorrentClient()
    print(f"   ✅ qBittorrent 客户端加载成功 - 状态: {qbit_client.stats is not None}")

    print("\n6. 测试 crawler 模块...")
    from magnet_harvester.crawler import MagnetCrawler, CrawlMetrics, BASE64_MAGNET_RE
    metrics = CrawlMetrics()
    print(f"   ✅ Crawler 加载成功 - 指标支持: {metrics is not None}")
    print(f"   - Base64 正则: {BASE64_MAGNET_RE.pattern[:50]}...")

    print("\n6.5 测试 store 模块 (新 ItemStore)...")
    from magnet_harvester.store import InMemoryItemStore, StoreStats
    store = InMemoryItemStore()
    from magnet_harvester.models import MagnetItem
    store.add(MagnetItem(hash="BEEF456", name="store测试", magnet="magnet:?xt=urn:btih:BEEF456"))
    print(f"   ✅ ItemStore 加载成功 - 条目数: {store.count}")

    print("\n6.6 测试 bus 模块 (新 MessageBus)...")
    from magnet_harvester.bus import MessageBus, Event, EventType
    bus = MessageBus()
    print(f"   ✅ MessageBus 加载成功 - 事件类型: {len(EventType)}")

    print("\n6.7 测试 pipeline 模块 (新 HarvestPipeline)...")
    from magnet_harvester.pipeline import HarvestPipeline
    print(f"   ✅ Pipeline 模块加载成功")
    
    print("\n7. 测试 agent 模块...")
    from magnet_harvester.agent import MagnetAgent, AGENT_TOOLS
    print(f"   ✅ Agent 加载成功 - 工具数: {len(AGENT_TOOLS)}")
    
    print("\n8. 测试 tts_client 模块...")
    from magnet_harvester.tts_client import MinimaxTTS
    tts_client = MinimaxTTS()
    print(f"   ✅ TTS 客户端加载成功")
    
    print("\n9. 测试 main 模块...")
    from magnet_harvester.main import app, stats, SystemStats
    print(f"   ✅ Main 应用加载成功")
    print(f"   - WebSocket 端点: /ws, /ws/chat")
    print(f"   - API 端点数: ~15 个")
    
    print("\n" + "="*50)
    print("🎉 所有模块导入成功！")
    print("="*50)
    
    print("\n📋 快速测试建议:")
    print("1. 复制 .env.test 为 .env 并填入真实配置")
    print("2. 确保 qBittorrent 服务运行中")
    print("3. 运行: python3 main.py")
    print("4. 访问: http://localhost:8899")
    
    sys.exit(0)
    
except Exception as e:
    print(f"\n❌ 导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
