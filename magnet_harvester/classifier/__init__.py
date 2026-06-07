"""
MiniMax 分类器 v2.0 — 包结构

子模块:
- fallback: 本地分类规则（LOCAL_RULES + 辅助函数）
- cache: ClassificationCache
- optimizer: BatchOptimizer
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import anthropic
import httpx

from magnet_harvester.classifier.cache import ClassificationCache
from magnet_harvester.classifier.fallback import (
    ALIASES,
    LOCAL_RULES,
    VALID_CATEGORIES,
    analyze_quality,
    classify_local,
    classify_local_with_confidence,
    make_fallback,
    normalize,
)
from magnet_harvester.classifier.optimizer import BatchOptimizer
from magnet_harvester.config import ClassifierConfig, settings

log = logging.getLogger(__name__)

MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"
MINIMAX_HTTP_BASE = "https://api.minimaxi.com"

SYSTEM_PROMPT = """你是专业的影视和数字资源分类专家。

分类选项（严格从这里选）：电影、电视剧、动漫、音乐、游戏、软件、综艺、纪录片、其他

规则：
- 电影：单部影片，无集数，含 1080p/4K/BluRay/HDR
- 电视剧：有 S01E01/Season/第X季/第X集/EP 等集数标注的真人剧集
- 动漫：日漫/国漫/OVA/BD/Bangumi/字幕组（即使有集数也归动漫）
- 音乐：FLAC/MP3/专辑/单曲/MV/OST/Soundtrack
- 游戏：GOTY/DLC/Repack/Crack/FitGirl/GOG/Steam
- 软件：Setup/Install/Crack/版本号/Office/Adobe
- 综艺：综艺节目/真人秀/选秀/脱口秀
- 纪录片：BBC/国家地理/Discovery/纪录片
- 其他：无法归入以上类别"""

BATCH_TOOL = {
    "name": "submit_classifications",
    "description": "提交批量资源分类结果，按 index 顺序",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index":      {"type": "integer"},
                        "category":   {"type": "string", "enum": VALID_CATEGORIES},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reason":     {"type": "string", "maxLength": 30},
                    },
                    "required": ["index", "category", "confidence"],
                },
            }
        },
        "required": ["results"],
    },
}

SINGLE_TOOL = {
    "name": "submit_classification",
    "description": "提交单条资源分类（支持 thinking 深度推理）",
    "input_schema": {
        "type": "object",
        "properties": {
            "category":   {"type": "string", "enum": VALID_CATEGORIES},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "reason":     {"type": "string", "maxLength": 30},
        },
        "required": ["category", "confidence"],
    },
}

_thinking_sem: asyncio.Semaphore | None = None


def _get_thinking_sem() -> asyncio.Semaphore:
    global _thinking_sem
    if _thinking_sem is None:
        _thinking_sem = asyncio.Semaphore(3)
    return _thinking_sem


# ── 结果处理函数 ──────────────────────────


def _make_result(tool_input: dict) -> dict:
    cat = tool_input.get("category", "其他")
    if cat not in VALID_CATEGORIES:
        cat = "其他"
    paths = settings.CATEGORY_PATHS
    return {
        "category":   cat,
        "confidence": tool_input.get("confidence", "medium"),
        "reason":     tool_input.get("reason", ""),
        "save_path":  paths.get(cat, paths["其他"]),
    }


def _extract_partial_results(partial_json: str) -> list[dict]:
    completed    = []
    seen_indexes = set()
    pattern = re.compile(
        r'"index"\s*:\s*(\d+).*?"category"\s*:\s*"([^"]+)".*?"confidence"\s*:\s*"([^"]+)"',
        re.DOTALL,
    )
    for m in pattern.finditer(partial_json):
        idx = int(m.group(1))
        if idx in seen_indexes:
            continue
        seen_indexes.add(idx)
        try:
            start = partial_json.rfind("{", 0, m.start() + 1)
            end   = partial_json.find("}", m.end() - 1) + 1
            obj   = json.loads(partial_json[start:end])
            completed.append(obj)
        except Exception:
            completed.append({
                "index":      idx,
                "category":   m.group(2),
                "confidence": m.group(3),
            })
    return completed


# ── UsageStats ──────────────────────────────


@dataclass
class UsageStats:
    input_tokens:  int = 0
    output_tokens: int = 0
    api_calls:     int = 0
    errors:        int = 0
    start_time:    float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict:
        return {
            "input_tokens":       self.input_tokens,
            "output_tokens":      self.output_tokens,
            "total_tokens":       self.total_tokens,
            "api_calls":          self.api_calls,
            "errors":             self.errors,
            "elapsed_sec":        round(time.time() - self.start_time, 1),
            "estimated_cost_cny": round(self.total_tokens / 1_000_000 * 4, 4),
        }


# ── MiniMaxClassifier ────────────────────────


class MiniMaxClassifier:
    def __init__(self, config: ClassifierConfig = None):
        if config is not None:
            self._config = config
        else:
            self._config = ClassifierConfig(
                api_key=settings.MINIMAX_API_KEY,
                model=settings.MINIMAX_MODEL,
                thinking_model=settings.MINIMAX_THINKING_MODEL,
                thinking_recheck=settings.THINKING_RECHECK,
            )
        self._client = anthropic.AsyncAnthropic(
            api_key     = self._config.api_key,
            base_url    = MINIMAX_BASE_URL,
            max_retries = 3,
            timeout     = anthropic.Timeout(connect=5, read=120, write=30, pool=5),
        )
        self.model = self._config.model
        self.usage = UsageStats()
        self._ok: bool | None = None
        self._cache = ClassificationCache(max_age_seconds=3600)
        self._batch_optimizer = BatchOptimizer(batch_size=20)
        self._total_cached = 0
        self._total_ai = 0
        self._total_local = 0

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(
                    f"{MINIMAX_HTTP_BASE}/v1/models",
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                )
                if r.status_code == 401:
                    log.error("MiniMax API Key 无效（401），请检查 .env 中的 MINIMAX_API_KEY")
                    self._ok = False
                    return False
                self._ok = r.status_code in (200, 404)
                return bool(self._ok)
        except Exception as e:
            log.warning(f"MiniMax ping 网络失败: {e}")
            self._ok = False
            return False

    def _track_usage(self, usage) -> None:
        if usage:
            self.usage.input_tokens  += usage.input_tokens
            self.usage.output_tokens += usage.output_tokens
            self.usage.api_calls     += 1

    async def classify_stream_batch(
        self,
        items: list[dict],
        on_result: Callable[[int, dict], None] | None = None,
    ) -> list[dict]:
        if not items:
            return []

        names_text    = "\n".join(f"{it['index']}. {it['name']}" for it in items)
        user_msg      = (
            f"请对以下 {len(items)} 个资源分类，"
            f"调用 submit_classifications 工具提交结果：\n\n{names_text}"
        )
        output_budget = min(8192, len(items) * 100 + 512)
        results: dict[int, dict] = {}

        try:
            accumulated = ""
            last_count  = 0

            async with self._client.messages.stream(
                model       = self.model,
                max_tokens  = output_budget,
                temperature = 0.1,
                top_p       = 0.1,
                system      = SYSTEM_PROMPT,
                tools       = [BATCH_TOOL],
                tool_choice = {"type": "tool", "name": "submit_classifications"},
                metadata    = {
                    "user_id":    "magnet-harvester",
                    "task_type":  "batch_classify",
                    "batch_size": str(len(items)),
                },
                messages = [{"role": "user", "content": user_msg}],
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "input_json_delta":
                            accumulated += delta.partial_json
                            new_results  = _extract_partial_results(accumulated)
                            if len(new_results) > last_count:
                                for r in new_results[last_count:]:
                                    idx    = r.get("index", -1)
                                    parsed = _make_result(r)
                                    results[idx] = parsed
                                    if on_result and idx >= 0:
                                        on_result(idx, parsed)
                                last_count = len(new_results)

                final = await stream.get_final_message()
                self._track_usage(final.usage)

                for block in final.content:
                    if block.type == "tool_use" and block.name == "submit_classifications":
                        for r in block.input.get("results", []):
                            idx = r.get("index", -1)
                            if idx not in results:
                                parsed = _make_result(r)
                                results[idx] = parsed
                                if on_result and idx >= 0:
                                    on_result(idx, parsed)

        except anthropic.RateLimitError:
            log.error("MiniMax 限速（已重试3次），切换本地规则")
            self.usage.errors += 1
        except anthropic.APIError as e:
            log.error(f"MiniMax API 错误: {e}")
            self.usage.errors += 1

        for it in items:
            if it["index"] not in results:
                fallback = make_fallback(it["name"])
                results[it["index"]] = fallback
                if on_result:
                    on_result(it["index"], fallback)

        return [results[it["index"]] for it in items]

    async def classify_one_thinking(self, name: str) -> dict:
        try:
            resp = await self._client.messages.create(
                model       = self._config.thinking_model,
                max_tokens  = 1024,
                temperature = 1.0,
                system      = SYSTEM_PROMPT,
                thinking    = {"type": "enabled", "budget_tokens": 512},
                tools       = [SINGLE_TOOL],
                tool_choice = {"type": "tool", "name": "submit_classification"},
                metadata    = {"user_id": "magnet-harvester", "task_type": "thinking_recheck"},
                messages    = [{"role": "user", "content": f"请仔细分析（名称可能不完整或含缩写）：\n{name}"}],
            )
            self._track_usage(resp.usage)
            for block in resp.content:
                if block.type == "tool_use":
                    return _make_result(block.input)
        except Exception as e:
            log.warning(f"thinking 分类失败 [{name}]: {e}")

        return make_fallback(name, "thinking_fallback")

    async def _recheck_with_limit(self, name: str) -> dict:
        async with _get_thinking_sem():
            return await self.classify_one_thinking(name)

    async def classify_batch(self, names: List[str]) -> List[dict]:
        if not names:
            return []

        cached_results = {}
        uncached_items = []

        for i, name in enumerate(names):
            cached = self._cache.get(name)
            if cached:
                cached_results[i] = cached
                self._total_cached += 1
            else:
                uncached_items.append({"index": i, "name": name})

        if uncached_items:
            results = await self.classify_stream_batch(uncached_items)

            for i, result in zip([item["index"] for item in uncached_items], results):
                self._cache.set(names[i], result)

        final_results = []
        for i in range(len(names)):
            if i in cached_results:
                final_results.append(cached_results[i])
            else:
                result = results.pop(0) if results else make_fallback(names[i], "batch_fallback")
                final_results.append(result)

        if self._config.thinking_recheck:
            recheck_indices = []
            for i, result in enumerate(final_results):
                if result.get("confidence") == "low" and names[i] not in cached_results:
                    recheck_indices.append(i)

            if recheck_indices:
                log.info(f"thinking 二次核验 {len(recheck_indices)} 条（并发≤3）")
                rechecked = await asyncio.gather(
                    *[self._recheck_with_limit(names[i]) for i in recheck_indices],
                    return_exceptions=True,
                )
                for idx, (i, r) in enumerate(zip(recheck_indices, rechecked)):
                    if isinstance(r, BaseException):
                        log.warning(f"thinking 核验失败 [{names[i]}]: {r}")
                    else:
                        final_results[i] = r
                        self._cache.set(names[i], r)

        return final_results

    def get_cache_stats(self) -> dict:
        return {
            "cache": self._cache.stats(),
            "total_cached": self._total_cached,
            "total_ai": self._total_ai,
            "total_local": self._total_local,
        }

    def clear_cache(self):
        self._cache.clear()
        log.info("分类缓存已清空")
