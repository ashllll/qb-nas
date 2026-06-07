"""
测试 Classifier 分解 — 验证拆包后各组件可独立工作
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier import MiniMaxClassifier


def test_original_import_works():
    """旧导入路径 `from magnet_harvester.classifier import MiniMaxClassifier` 仍有效"""
    from magnet_harvester.classifier import MiniMaxClassifier
    assert MiniMaxClassifier is not None


def test_cache_independent():
    """ClassificationCache 可独立导入和使用"""
    from magnet_harvester.classifier.cache import ClassificationCache
    cache = ClassificationCache()
    assert cache is not None
    # 验证基本功能：set 后能 get
    cache.set("test_hash", {"category": "电影", "confidence": "high"})
    result = cache.get("test_hash")
    assert result is not None
    assert result["category"] == "电影"


def test_optimizer_independent():
    """BatchOptimizer 可独立导入和使用"""
    from magnet_harvester.classifier.optimizer import BatchOptimizer
    optimizer = BatchOptimizer()
    assert optimizer is not None


def test_fallback_independent():
    """Local fallback 规则可独立导入"""
    from magnet_harvester.classifier.fallback import LOCAL_RULES, classify_local
    assert len(LOCAL_RULES) > 0
    result = classify_local("Test Movie 2024 1080p")
    assert result is not None
    assert isinstance(result, str)


if __name__ == "__main__":
    test_original_import_works()
    test_cache_independent()
    test_optimizer_independent()
    test_fallback_independent()
    print("=== Classifier decomposition tests passed! ===")
