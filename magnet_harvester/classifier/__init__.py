"""
MiniMax 分类器 v2.0 — 包结构 (本地规则版)

子模块:
- fallback: 本地分类规则（LOCAL_RULES + 辅助函数）
- cache: ClassificationCache
- optimizer: BatchOptimizer
- local_classifier: LocalClassifier（主要分类器实现）
"""
from __future__ import annotations

from magnet_harvester.classifier.cache import ClassificationCache
from magnet_harvester.classifier.fallback import (
    ALIASES,
    LOCAL_RULES,
    QUALITY_PATTERNS,
    RESOLUTION_PATTERNS,
    VALID_CATEGORIES,
    analyze_quality,
    classify_local,
    classify_local_with_confidence,
    make_fallback,
    normalize,
)
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.classifier.optimizer import BatchOptimizer
from magnet_harvester.usage_stats import UsageStats

__all__ = [
    "LocalClassifier",
    "ClassificationCache",
    "BatchOptimizer",
    "LOCAL_RULES",
    "VALID_CATEGORIES",
    "classify_local",
    "make_fallback",
]
