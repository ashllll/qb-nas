"""
LocalClassifier — 纯本地规则分类器，零外部依赖

使用 ClassificationRule 链：KeywordRule → StudioRule → FallbackRule。
规则链可配置，优先级显式，每个规则返回统一的 ClassificationResult。
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable, List

from magnet_harvester.classifier.rule import (
    ClassificationRule,
    FallbackRule,
    KeywordRule,
    StudioRule,
)

log = logging.getLogger(__name__)


class LocalClassificationEngine:
    """Classifies names through the configured local rule chain.

    内置按名称的 LRU 结果缓存（默认启用，上限 ``CACHE_MAX_SIZE`` 条），
    并提供 get_cache_stats()/clear_cache() 真实统计与清理。
    """

    CACHE_MAX_SIZE = 1024

    def __init__(
        self,
        rule_chain: List[ClassificationRule] | None = None,
        keyword_rules_file: Path | None = None,
        cache_enabled: bool = True,
    ):
        if rule_chain is not None:
            self._rule_chain = rule_chain
        else:
            # Default: keyword → studio → fallback
            self._rule_chain = [
                KeywordRule(rules_file=keyword_rules_file),
                StudioRule(),
                FallbackRule(),
            ]
        self._cache_enabled = cache_enabled
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_lock = threading.Lock()

    def classify_name(self, name: str) -> dict:
        """分类单个名称：按规则链优先级匹配，返回统一结果格式。"""
        if self._cache_enabled:
            with self._cache_lock:
                cached = self._cache.get(name)
                if cached is not None:
                    self._cache.move_to_end(name)
                    self._cache_hits += 1
                else:
                    self._cache_misses += 1
            if cached is not None:
                return dict(cached)

        result = self._classify_uncached(name)
        if self._cache_enabled:
            with self._cache_lock:
                self._cache[name] = dict(result)
                self._cache.move_to_end(name)
                while len(self._cache) > self.CACHE_MAX_SIZE:
                    self._cache.popitem(last=False)
        return result

    def _classify_uncached(self, name: str) -> dict:
        """走完整规则链分类（不经过缓存）。"""
        for rule in self._rule_chain:
            try:
                result = rule.apply(name)
            except Exception:
                log.exception("规则 %s 处理名称 '%s' 时出错，已跳过", type(rule).__name__, name)
                continue
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

    def get_cache_stats(self) -> dict:
        """返回缓存统计：size / hits / misses / hit_rate_percent。"""
        with self._cache_lock:
            size = len(self._cache)
            hits = self._cache_hits
            misses = self._cache_misses
        total = hits + misses
        hit_rate = round(100.0 * hits / total, 1) if total else 0.0
        return {
            "cache": {
                "size": size,
                "hits": hits,
                "misses": misses,
                "hit_rate_percent": hit_rate,
            }
        }

    def clear_cache(self) -> None:
        """清空缓存并重置统计计数。"""
        with self._cache_lock:
            self._cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0

    def reload_rules(self) -> int:
        """Reload file-backed rules in the chain, returning the count reloaded.

        规则变更后旧分类结果可能失效，因此先清空缓存。
        """
        reloaded = 0
        for rule in self._rule_chain:
            reload_rule = getattr(rule, "reload", None)
            if reload_rule is not None and reload_rule():
                reloaded += 1
        if reloaded:
            self.clear_cache()
        return reloaded


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
        keyword_rules_file: Path | None = None,
    ):
        self.usage = _NullUsageStats()
        self._ok = True
        self._engine = engine or LocalClassificationEngine(
            rule_chain=rule_chain,
            keyword_rules_file=keyword_rules_file,
        )

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
        return [self._engine.classify_name(item.get("name", "")) for item in items]

    def classify_one(self, name: str) -> dict:
        """单条分类"""
        return self._engine.classify_name(name)

    def reload_rules(self) -> dict:
        """Reload file-backed local classification rules."""
        return {
            "status": "reloaded",
            "rules_reloaded": self._engine.reload_rules(),
        }

    # ── 缓存统计/清理（委托真实 engine 缓存） ────

    def get_cache_stats(self) -> dict:
        """缓存统计：真实反映 engine 的命中/未命中与当前条目数。"""
        return self._engine.get_cache_stats()

    def clear_cache(self) -> None:
        """清空分类结果缓存并重置统计。"""
        self._engine.clear_cache()

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
