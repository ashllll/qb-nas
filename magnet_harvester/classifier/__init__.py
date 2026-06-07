"""
LocalClassifier — 本地规则分类器

子模块:
- fallback: 本地分类规则（LOCAL_RULES + 辅助函数）
- local_classifier: LocalClassifier（主要分类器实现）
"""
from __future__ import annotations

from magnet_harvester.classifier.fallback import (
    LOCAL_RULES,
    VALID_CATEGORIES,
    classify_local,
    make_fallback,
)
from magnet_harvester.classifier.local_classifier import LocalClassifier

__all__ = [
    "LocalClassifier",
    "LOCAL_RULES",
    "VALID_CATEGORIES",
    "classify_local",
    "make_fallback",
]
