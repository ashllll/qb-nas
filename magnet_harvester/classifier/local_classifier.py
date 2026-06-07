"""
LocalClassifier — 纯本地规则分类器，零外部依赖

直接使用 LOCAL_RULES 正则进行分类，同步 API。
符合 ClassifyPhase 协议，可替换 MiniMaxClassifier。
优先识别成人厂牌（StudioRecognizer），回退到 LOCAL_RULES。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from magnet_harvester.classifier.fallback import (
    LOCAL_RULES,
    VALID_CATEGORIES,
    classify_local,
    make_fallback,
)
from magnet_harvester.studio_recognizer import StudioRecognizer

log = logging.getLogger(__name__)


class LocalClassifier:
    """纯本地规则分类器。

    公共接口（符合 ClassifyPhase 协议）:
        classify_stream_batch(items, on_result) — 流式批量分类
        classify_sync_batch(items)              — 同步批量分类（便捷）
        classify_one(name)                      — 单条分类
        usage                                   — 占位 UsageStats
        get_cache_stats()                       — 占位
        clear_cache()                           — 空操作
    """

    def __init__(self):
        self.usage = _NullUsageStats()
        self._ok = True
        self._studio_recognizer = StudioRecognizer()

    def _classify_name(self, name: str) -> dict:
        """分类单个名称：优先厂牌识别，回退本地规则"""
        # 先查成人厂牌
        studio = self._studio_recognizer.recognize(name)
        if studio:
            return {
                "category": studio["name"],
                "confidence": "high",
                "reason": "adult_studio",
                "save_path": studio["save_path"],
            }
        # 回退 LOCAL_RULES
        return make_fallback(name, "local_rule")

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
            result = self._classify_name(item.get("name", ""))
            results.append(result)
            if on_result and idx >= 0:
                on_result(idx, result)
        return results

    # ── 便捷方法 ──────────────────────────

    def classify_sync_batch(self, items: list[dict]) -> list[dict]:
        """同步批量分类（非协程版本）"""
        return [
            self._classify_name(item.get("name", ""))
            for item in items
        ]

    def classify_one(self, name: str) -> dict:
        """单条分类"""
        return self._classify_name(name)

    # ── 兼容方法（原 MiniMaxClassifier 的接口）────

    def get_cache_stats(self) -> dict:
        return {"cache": {"size": 0, "hits": 0, "misses": 0, "hit_rate_percent": 0.0}}

    def clear_cache(self):
        pass

    async def ping(self) -> bool:
        return True


class _NullUsageStats:
    """占位 UsageStats — 保持与 MiniMaxClassifier 兼容"""
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
