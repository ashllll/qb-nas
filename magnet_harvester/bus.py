"""
MessageBus — 类型化事件总线（深模块）

接口：emit(event) — 背后是 fan-out + 过滤 + 序列化。
每个适配器独立订阅，调用者不需要知道谁在监听。

适配器：
- WebSocket 适配器：推送到实时 UI
- TTS 适配器：语音通知
- Log 适配器：事件审计（未来）
- NullBus：测试用静默总线
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

log = logging.getLogger(__name__)


class EventType(str, Enum):
    CRAWL_START = "crawl_start"
    CRAWL_PROGRESS = "crawl_progress"
    CRAWL_ERROR = "crawl_error"
    CRAWL_DONE = "crawl_done"
    MAGNET_FOUND = "magnet_found"
    CLASSIFY_START = "classify_start"
    CLASSIFY_DONE = "classify_done"
    CLASSIFY_ALL_DONE = "classify_all_done"
    DOWNLOAD_START = "download_start"
    DOWNLOAD_RESULT = "download_result"
    DOWNLOAD_DONE = "download_done"
    USAGE_UPDATE = "usage_update"
    AGENT_TOKEN = "agent_token"
    AGENT_TOOL_CALL = "agent_tool_call"
    AGENT_DONE = "agent_done"
    AGENT_ERROR = "agent_error"
    ERROR = "error"
    STORE_CHANGED = "store_changed"


@dataclass
class Event:
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"type": self.type.value, **self.data}


# 订阅者回调: async def callback(event: Event) -> None
Subscriber = Callable[[Event], Coroutine[Any, Any, None]]


class MessageBus:
    """类型化事件总线。

    接口: emit(event) | subscribe(type, callback) | unsubscribe(type, callback)

    发射一次 emit，所有匹配订阅者并发接收。
    """

    def __init__(self):
        self._subscribers: Dict[EventType, List[Subscriber]] = {}
        self._global_subscribers: List[Subscriber] = []

    def subscribe(self, event_type: EventType | None, callback: Subscriber):
        """订阅事件。event_type=None 表示订阅全部事件。"""
        if event_type is None:
            self._global_subscribers.append(callback)
        else:
            self._subscribers.setdefault(event_type, []).append(callback)

    def unsubscribe(self, event_type: EventType | None, callback: Subscriber):
        if event_type is None:
            self._global_subscribers = [c for c in self._global_subscribers if c != callback]
        elif event_type in self._subscribers:
            self._subscribers[event_type] = [c for c in self._subscribers[event_type] if c != callback]

    async def emit(self, event: Event):
        """发射事件到所有匹配的订阅者（并发执行）"""
        tasks: List[asyncio.Task] = []

        for cb in self._global_subscribers:
            tasks.append(asyncio.create_task(self._safe_call(cb, event), name=f"bus:global"))

        for cb in self._subscribers.get(event.type, []):
            tasks.append(asyncio.create_task(self._safe_call(cb, event), name=f"bus:{event.type.value}"))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def emit_nowait(self, event: Event):
        """发射事件但不等待订阅者完成（fire-and-forget）"""
        asyncio.create_task(self.emit(event))

    @staticmethod
    async def _safe_call(cb: Subscriber, event: Event):
        try:
            await cb(event)
        except Exception:
            log.debug(f"MessageBus 订阅者异常: {event.type.value}", exc_info=True)


class NullBus(MessageBus):
    """测试用静默总线 — 第二个适配器证明缝是真实的"""

    async def emit(self, event: Event):
        pass

    async def emit_nowait(self, event: Event):
        pass
