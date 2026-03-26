"""
MiniMax 分类器
- AsyncAnthropic + tool_use(forced) + streaming + thinking
- max_retries=3 内置指数退避
- usage 追踪（as_dict() 方法，区别于 Pydantic model_dump）
- 懒初始化 Semaphore（Python 3.10+ 事件循环兼容）
- 所有路径读取均通过 settings.CATEGORY_PATHS，不做模块级快照
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, List

import anthropic
import httpx

from config import settings

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
    (r's\d{1,2}e\d{1,2}|season\s*\d|第[一二三四五六七八九十\d]+季|第.{1,4}集|ep\d+', "电视剧"),
    (r'动漫|动画|anime|ova\b|bangumi|字幕组|[简繁]体字幕', "动漫"),
    (r'flac|mp3|aac|专辑|单曲|ost\b|soundtrack|album\b', "音乐"),
    (r'\bgame\b|goty|dlc\b|repack|codex|skidrow|fitgirl|gog\b', "游戏"),
    (r'setup\.exe|installer|crack|keygen|adobe\s|office\s|v\d+\.\d+\.\d+', "软件"),
    (r'documentary|纪录片|bbc\b|national.geo|discovery\b', "纪录片"),
    (r'综艺|真人秀|选秀|variety.show|reality.show', "综艺"),
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


class MiniMaxClassifier:
    def __init__(self):
        self._client = anthropic.AsyncAnthropic(
            api_key     = settings.MINIMAX_API_KEY,
            base_url    = MINIMAX_BASE_URL,
            max_retries = 3,
            timeout     = anthropic.Timeout(connect=5, read=120, write=30, pool=5),
        )
        self.model = settings.MINIMAX_MODEL
        self.usage = UsageStats()
        self._ok: bool | None = None

    async def ping(self) -> bool:
        """轻量健康检查，不消耗生成 token"""
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(
                    f"{MINIMAX_HTTP_BASE}/v1/models",
                    headers={"Authorization": f"Bearer {settings.MINIMAX_API_KEY}"},
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
                model       = settings.MINIMAX_THINKING_MODEL,
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
        items   = [{"index": i, "name": n} for i, n in enumerate(names)]
        results = await self.classify_stream_batch(items)

        if settings.THINKING_RECHECK:
            recheck = [(i, names[i]) for i, r in enumerate(results)
                       if r.get("confidence") == "low"]
            if recheck:
                log.info(f"thinking 二次核验 {len(recheck)} 条（并发≤3）")
                # return_exceptions=True: 单条失败不会取消其余请求
                rechecked = await asyncio.gather(
                    *[self._recheck_with_limit(n) for _, n in recheck],
                    return_exceptions=True,
                )
                for (i, orig_name), r in zip(recheck, rechecked):
                    if isinstance(r, BaseException):
                        log.warning(f"thinking 核验失败 [{orig_name}]: {r}")
                        # 保留原始 low-confidence 结果，不替换
                    else:
                        results[i] = r

        return results


classifier = MiniMaxClassifier()
