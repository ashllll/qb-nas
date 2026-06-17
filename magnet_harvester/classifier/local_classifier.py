"""
LocalClassifier — 纯本地规则分类器，零外部依赖

使用 ClassificationRule 链：KeywordRule → StudioRule → FallbackRule。
规则链可配置，优先级显式，每个规则返回统一的 ClassificationResult。
"""
from __future__ import annotations

import logging
from typing import Callable, List

from magnet_harvester.classifier.rule import (
    ClassificationRule,
    FallbackRule,
    KeywordRule,
    StudioRule,
)

log = logging.getLogger(__name__)


class LocalClassificationEngine:
    """Classifies names through the configured local rule chain."""

    def __init__(self, rule_chain: List[ClassificationRule] | None = None):
        if rule_chain is not None:
            self._rule_chain = rule_chain
        else:
            # Default: keyword → studio → fallback
            self._rule_chain = [
                KeywordRule(),
                StudioRule(),
                FallbackRule(),
            ]

    def classify_name(self, name: str) -> dict:
        """分类单个名称：按规则链优先级匹配，返回统一结果格式。"""
        for rule in self._rule_chain:
            result = rule.apply(name)
            if result is not None:
                return {
                    "category": result.category,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "save_path": result.save_path,
                }
        # FallbackRule always returns, so we should never reach here
        return {
            "category": "其他",
            "confidence": "low",
            "reason": "no_match",
            "save_path": "其他",
        }


class LocalClassifier:
    """ClassifyPhase adapter for the local rule engine.

    Local classification itself lives in LocalClassificationEngine; this class
    keeps the pipeline compatibility surface (`usage`, cache stats, streaming
    callbacks) at the adapter seam.
    """

    def __init__(
        self,
        rule_chain: List[ClassificationRule] | None = None,
        engine: LocalClassificationEngine | None = None,
    ):
        self.usage = _NullUsageStats()
        self._ok = True
        self._engine = engine or LocalClassificationEngine(rule_chain=rule_chain)

    # ── 协议方法 ──────────────────────────

    async def classify_stream_batch(
        self,
        items: list[dict],
        on_result: Callable[[int, dict], None] | None = None,
    ) -> list[dict]:
        """流式批量分类 — 同步实现，直接遍历并回调"""
        results: list[dict] = []
        for item in items:
            idx = item.get("index", -1)
            result = self._engine.classify_name(item.get("name", ""))
            results.append(result)
            if on_result and idx >= 0:
                on_result(idx, result)
        return results

    # ── 便捷方法 ──────────────────────────

    def classify_sync_batch(self, items: list[dict]) -> list[dict]:
        """同步批量分类（非协程版本）"""
        return [
            self._engine.classify_name(item.get("name", ""))
            for item in items
        ]

    def classify_one(self, name: str) -> dict:
        """单条分类"""
        return self._engine.classify_name(name)

    # ── 兼容方法 ────

    def get_cache_stats(self) -> dict:
        return {"cache": {"size": 0, "hits": 0, "misses": 0, "hit_rate_percent": 0.0}}

    def clear_cache(self):
        pass

    async def ping(self) -> bool:
        return True


class _NullUsageStats:
    """占位 UsageStats — 保持分类器协议兼容"""
    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0
    errors: int = 0

    def as_dict(self) -> dict:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "api_calls": 0,
            "errors": 0,
            "elapsed_sec": 0.0,
            "estimated_cost_cny": 0.0,
        }
