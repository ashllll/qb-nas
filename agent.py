"""
Magnet Agent — Agentic Loop
- 自然语言指令 → MiniMax → tool_use → 执行 → 循环直到 end_turn
- 滑动窗口裁剪历史（防止超出 204K context window）
- 共享 UsageStats，用量统一上报到 /api/usage
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

import anthropic

from classifier import UsageStats
from config import settings

log = logging.getLogger(__name__)

MINIMAX_BASE_URL  = "https://api.minimaxi.com/anthropic"
MAX_HISTORY_TURNS = 20   # 保留最近 N 轮，每轮 2 条消息

AGENT_TOOLS = [
    {
        "name": "get_stats",
        "description": "获取当前磁力列表的统计信息：各分类数量、下载状态分布、总数等",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_items",
        "description": "列出磁力链接列表，支持按分类和状态过滤",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "按分类过滤，留空返回全部"},
                "status":   {"type": "string", "enum": ["pending", "success", "error", "all"], "default": "all"},
                "limit":    {"type": "integer", "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "start_crawl",
        "description": "对指定 URL 启动磁力爬取任务，爬完自动分类",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":   {"type": "string"},
                "depth": {"type": "integer", "default": 1},
            },
            "required": ["url"],
        },
    },
    {
        "name": "add_to_queue",
        "description": "将指定 hash 的磁力链接发送到 qBittorrent。传 ['all'] 下载全部待处理",
        "input_schema": {
            "type": "object",
            "properties": {
                "hashes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["hashes"],
        },
    },
    {
        "name": "reclassify_item",
        "description": "修改某条磁力的分类（手动纠错）",
        "input_schema": {
            "type": "object",
            "properties": {
                "hash":     {"type": "string", "description": "磁力 hash（支持前缀，至少8位）"},
                "category": {
                    "type": "string",
                    "enum": ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"],
                },
            },
            "required": ["hash", "category"],
        },
    },
    {
        "name": "search_items",
        "description": "在当前列表中按名称关键词搜索磁力链接",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "clear_all",
        "description": "清空当前磁力列表",
        "input_schema": {
            "type": "object",
            "properties": {"confirm": {"type": "boolean", "description": "必须传 true 才执行"}},
            "required": ["confirm"],
        },
    },
]

AGENT_SYSTEM = """你是 Magnet Harvester 的智能下载助手，通过工具帮用户管理 NAS 磁力下载队列。

行为准则：
- 批量下载前先用 list_items 确认内容和数量
- 操作完成后汇报结果
- 用简洁中文回复
- 遇到 URL 直接调用 start_crawl"""


def _trim_history(history: list[dict]) -> list[dict]:
    """
    滑动窗口裁剪历史，保留最近 MAX_HISTORY_TURNS 轮。
    裁剪后确保首条是 user 角色（Anthropic 格式要求）。
    """
    max_msgs = MAX_HISTORY_TURNS * 2
    if len(history) <= max_msgs:
        return history
    trimmed = history[-max_msgs:]
    while trimmed and trimmed[0]["role"] != "user":
        trimmed = trimmed[1:]
    return trimmed


class MagnetAgent:
    MAX_TURNS = 8

    def __init__(
        self,
        tool_executor: Callable[[str, dict], Any],
        shared_usage:  UsageStats | None = None,
    ):
        self._executor = tool_executor
        self._client   = anthropic.AsyncAnthropic(
            api_key     = settings.MINIMAX_API_KEY,
            base_url    = MINIMAX_BASE_URL,
            max_retries = 3,
            timeout     = anthropic.Timeout(connect=5, read=120, write=30, pool=5),
        )
        self.model = settings.MINIMAX_MODEL
        self.usage = shared_usage or UsageStats()

    async def close(self):
        """释放 HTTP 连接池，WebSocket 断开时调用"""
        await self._client.close()

    async def run(
        self,
        user_message:  str,
        history:       list[dict],
        on_token:      Callable[[str], None]        | None = None,
        on_tool_call:  Callable[[str, dict], None]  | None = None,
        on_usage:      Callable[[dict], None]       | None = None,
    ) -> tuple[str, list[dict]]:
        messages   = _trim_history(history) + [{"role": "user", "content": user_message}]
        final_text = ""

        for turn in range(self.MAX_TURNS):
            text_parts: list[str] = []

            async with self._client.messages.stream(
                model       = self.model,
                max_tokens  = 2048,
                temperature = 0.7,
                system      = AGENT_SYSTEM,
                tools       = AGENT_TOOLS,
                tool_choice = {"type": "auto"},
                metadata    = {"user_id": "harvester", "session_turn": str(turn)},
                messages    = messages,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta" and delta.text:
                            text_parts.append(delta.text)
                            if on_token:
                                on_token(delta.text)

                final_msg = await stream.get_final_message()

            if final_msg.usage:
                self.usage.input_tokens  += final_msg.usage.input_tokens
                self.usage.output_tokens += final_msg.usage.output_tokens
                self.usage.api_calls     += 1
                if on_usage:
                    on_usage(self.usage.as_dict())

            messages.append({"role": "assistant", "content": final_msg.content})

            if final_msg.stop_reason == "end_turn":
                final_text = "".join(text_parts)
                break

            if final_msg.stop_reason != "tool_use":
                log.warning(f"意外的 stop_reason: {final_msg.stop_reason}")
                final_text = "".join(text_parts)
                break

            tool_results: list[dict] = []
            for block in final_msg.content:
                if block.type != "tool_use":
                    continue

                log.info(f"Agent 工具: {block.name}({json.dumps(block.input, ensure_ascii=False)[:80]})")
                if on_tool_call:
                    on_tool_call(block.name, block.input)

                is_error   = False
                result_str = ""
                try:
                    result     = await self._executor(block.name, block.input)
                    result_str = json.dumps(result, ensure_ascii=False)
                except Exception as e:
                    result_str = f"工具执行失败: {e}"
                    is_error   = True
                    self.usage.errors += 1
                    log.error(f"工具 {block.name} 异常: {e}")

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result_str,
                    **({"is_error": True} if is_error else {}),
                })

            messages.append({"role": "user", "content": tool_results})

        else:
            log.warning(f"Agent 达到最大轮次 {self.MAX_TURNS}")
            final_text = "（步骤较多，已截断）" + "".join(text_parts)

        return final_text, messages
