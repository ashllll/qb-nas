"""
MiniMax 分类器 v2.0
- AsyncAnthropic + tool_use(forced) + streaming + thinking
- max_retries=3 内置指数退避
- usage 追踪（as_dict() 方法，区别于 Pydantic model_dump）
- 懒初始化 Semaphore（Python 3.10+ 事件循环兼容）
- 所有路径读取均通过 settings.CATEGORY_PATHS，不做模块级快照
- 新增：哈希缓存、批量优化、智能重分类、置信度分析
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set
from datetime import datetime, timedelta

import anthropic
import httpx

from magnet_harvester.config import ClassifierConfig, settings

log = logging.getLogger(__name__)

MINIMAX_BASE_URL  = "https://api.minimaxi.com/anthropic"
MINIMAX_HTTP_BASE = "https://api.minimaxi.com"

VALID_CATEGORIES = ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"]

ALIASES = {
    "movie": "电影", "movies": "电影", "film": "电影",
    "tv": "电视剧", "drama": "电视剧", "series": "电视剧",
    "anime": "动漫", "animation": "动漫", "cartoon": "动漫",
    "music": "音乐", "audio": "音乐", "album": "音乐",
    "game": "游戏", "games": "游戏",
    "software": "软件", "app": "软件",
    "variety": "综艺",
    "documentary": "纪录片", "doc": "纪录片",
    "other": "其他", "others": "其他",
}

LOCAL_RULES = [
    (r's\d{1,2}e\d{1,2}|season\s*\d|第[一二三四五六七八九十\d]+季|第.{1,4}集|ep\d+|全\d+集|第\d+部', "电视剧"),
    (r'动漫|动画|anime|ova\b|bangumi|字幕组|[简繁]体字幕|国漫|日漫|剧场版', "动漫"),
    (r'flac|mp3|aac|wav|dff|dsd|专辑|单曲|ost\b|soundtrack|album\b|hires|hi-res|黑胶', "音乐"),
    (r'\bgame\b|goty|dlc\b|repack|codex|skidrow|fitgirl|gog\b|steam|破解版', "游戏"),
    (r'setup\.exe|installer|crack|keygen|adobe\s|office\s|v\d+\.\d+\.\d+|绿色版|便携版', "软件"),
    (r'documentary|纪录片|bbc\b|national.geo|discovery\b|探索频道|国家地理', "纪录片"),
    (r'综艺|真人秀|选秀|variety.show|reality.show|脱口秀|奇葩说', "综艺"),
]

QUALITY_PATTERNS = [
    (r'2160p|4k|uhd|3840', '4K'),
    (r'1080p|full\s*hd|fhd|1920', '1080P'),
    (r'720p|hd\s*ready|1280', '720P'),
    (r'576p|480p|sd\b|dvd', 'SD'),
]

RESOLUTION_PATTERNS = [
    (r'hdr\d{0,2}|dolby\s*vision|dv\b', 'HDR'),
    (r'blu-?ray|bluray|bdrip|bdr', 'BluRay'),
    (r'web-?dl|webrip|netflix|hulu|amazon', 'WEB'),
    (r'hdtv|tvrip|dvb', 'TV'),
    (r'dvdrip|dvd', 'DVD'),
    (r'web-?dl\s*\d{4}|wb-?dl', 'WEB-DL'),
]

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

# 懒初始化 Semaphore，避免在事件循环启动前创建（Python 3.10+ DeprecationWarning）
_thinking_sem: asyncio.Semaphore | None = None


def _get_thinking_sem() -> asyncio.Semaphore:
    global _thinking_sem
    if _thinking_sem is None:
        _thinking_sem = asyncio.Semaphore(3)
    return _thinking_sem


class ClassificationCache:
    def __init__(self, max_age_seconds: int = 3600):
        self._cache: Dict[str, dict] = {}
        self._timestamps: Dict[str, datetime] = {}
        self._max_age = timedelta(seconds=max_age_seconds)
        self._hits = 0
        self._misses = 0
    
    def _make_key(self, name: str) -> str:
        normalized = re.sub(r'[^\w\u4e00-\u9fff]', '', name.lower())
        return hashlib.md5(normalized.encode()).hexdigest()
    
    def get(self, name: str) -> Optional[dict]:
        key = self._make_key(name)
        if key in self._cache:
            if datetime.now() - self._timestamps[key] < self._max_age:
                self._hits += 1
                return self._cache[key].copy()
            else:
                del self._cache[key]
                del self._timestamps[key]
        self._misses += 1
        return None
    
    def set(self, name: str, result: dict):
        key = self._make_key(name)
        self._cache[key] = result.copy()
        self._timestamps[key] = datetime.now()
    
    def invalidate(self, name: str):
        key = self._make_key(name)
        self._cache.pop(key, None)
        self._timestamps.pop(key, None)
    
    def clear(self):
        self._cache.clear()
        self._timestamps.clear()
        self._hits = 0
        self._misses = 0
    
    def stats(self) -> dict:
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
            "hit_rate_percent": round(hit_rate, 1),
        }


class BatchOptimizer:
    def __init__(self, batch_size: int = 20, max_batch_size: int = 50):
        self.batch_size = batch_size
        self.max_batch_size = max_batch_size
    
    def optimize_batch(self, items: List[dict]) -> List[List[dict]]:
        if not items:
            return []
        
        items_with_priority = []
        for item in items:
            name = item.get("name", "")
            priority = self._calculate_priority(name)
            items_with_priority.append((priority, item, name))
        
        items_with_priority.sort(key=lambda x: x[0], reverse=True)
        
        high_priority = []
        medium_priority = []
        low_priority = []
        
        for priority, item, name in items_with_priority:
            if priority > 5:
                high_priority.append(item)
            elif priority > 2:
                medium_priority.append(item)
            else:
                low_priority.append(item)
        
        batches = []
        
        for group in [high_priority, medium_priority, low_priority]:
            for i in range(0, len(group), self.batch_size):
                batch = group[i:i + self.batch_size]
                if batch:
                    batches.append(batch)
        
        return batches
    
    def _calculate_priority(self, name: str) -> int:
        name_lower = name.lower()
        priority = 0
        
        if any(kw in name_lower for kw in ['s01e', 'season', 'ep01', '第', '集']):
            priority += 5
        if any(kw in name_lower for kw in ['动漫', 'anime', 'ova', 'bd']):
            priority += 4
        if any(kw in name_lower for kw in ['movie', 'film', '2024', '2023', '2025']):
            priority += 3
        if any(kw in name_lower for kw in ['game', 'software', 'crack']):
            priority += 3
        if any(kw in name_lower for kw in ['flac', 'mp3', 'album', 'ost']):
            priority += 2
        
        if re.search(r'2160p|4k|blu-?ray|bluray', name_lower):
            priority += 1
        if re.search(r'complete|全|完整|全集', name_lower):
            priority += 1
        
        return priority


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
        """转为可序列化字典（命名为 as_dict 避免与 Pydantic .dict() 混淆）"""
        return {
            "input_tokens":       self.input_tokens,
            "output_tokens":      self.output_tokens,
            "total_tokens":       self.total_tokens,
            "api_calls":          self.api_calls,
            "errors":             self.errors,
            "elapsed_sec":        round(time.time() - self.start_time, 1),
            "estimated_cost_cny": round(self.total_tokens / 1_000_000 * 4, 4),
        }


def _normalize(raw: str) -> str:
    raw = raw.strip().strip('"\'')
    if raw in settings.CATEGORY_PATHS:
        return raw
    return ALIASES.get(raw.lower().replace(" ", ""), "其他")


def _local_classify(name: str) -> str:
    n = name.lower()
    for pattern, cat in LOCAL_RULES:
        if re.search(pattern, n, re.IGNORECASE):
            return cat
    return "电影"


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


def _make_fallback(name: str, reason: str = "local_fallback") -> dict:
    cat   = _local_classify(name)
    paths = settings.CATEGORY_PATHS
    return {
        "category":   cat,
        "confidence": "low",
        "reason":     reason,
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


def _analyze_quality(name: str) -> dict:
    n = name.lower()
    quality = None
    source = None
    
    for pattern, q in QUALITY_PATTERNS:
        if re.search(pattern, n):
            quality = q
            break
    
    for pattern, s in RESOLUTION_PATTERNS:
        if re.search(pattern, n):
            source = s
            break
    
    return {"quality": quality, "source": source}


def _local_classify_with_confidence(name: str) -> tuple[str, str]:
    n = name.lower()
    matched_rules = []
    
    for pattern, cat in LOCAL_RULES:
        if re.search(pattern, n, re.IGNORECASE):
            matched_rules.append((pattern, cat))
    
    if not matched_rules:
        return "电影", "medium"
    
    primary_category = matched_rules[0][1]
    
    if len(matched_rules) >= 2:
        return primary_category, "high"
    elif any(kw in n for kw in ['complete', '全', '完整', '全集']):
        return primary_category, "high"
    else:
        return primary_category, "medium"


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
        """轻量健康检查，不消耗生成 token"""
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
        """流式批量分类，每完成一条立即回调 on_result(index, result)"""
        if not items:
            return []

        names_text    = "\n".join(f"{it['index']}. {it['name']}" for it in items)
        user_msg      = (
            f"请对以下 {len(items)} 个资源分类，"
            f"调用 submit_classifications 工具提交结果：\n\n{names_text}"
        )
        # 每条结果约 80-100 token，给足输出空间
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
                fallback = _make_fallback(it["name"])
                results[it["index"]] = fallback
                if on_result:
                    on_result(it["index"], fallback)

        return [results[it["index"]] for it in items]

    async def classify_one_thinking(self, name: str) -> dict:
        """开启 thinking 对模糊资源名深度推理"""
        try:
            resp = await self._client.messages.create(
                model       = self._config.thinking_model,
                max_tokens  = 1024,
                temperature = 1.0,   # thinking 必须为 1.0
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

        return _make_fallback(name, "thinking_fallback")

    async def _recheck_with_limit(self, name: str) -> dict:
        """限制 thinking 并发数（≤3），防止触发 429"""
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
                result = results.pop(0) if results else _make_fallback(names[i], "batch_fallback")
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

