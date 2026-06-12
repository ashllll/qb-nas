"""
MessageBus — 类型化事件总线

接口: emit(event) — 异步 fan-out 到所有订阅者。
适配器: NullBus — 测试用静默总线。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Coroutine, Dict, List, Optional

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
    ERROR = "error"
    STORE_CHANGED = "store_changed"
    ITEMS_CLEARED = "items_cleared"


@dataclass
class Event:
    type: EventType
    data: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"type": self.type.value, **self.data}


Subscriber = Callable[[Event], Coroutine[object, object, None]]


class MessageBus:
    def __init__(self):
        self._subscribers: Dict[EventType, List[Subscriber]] = {}
        self._global_subscribers: List[Subscriber] = []

    def subscribe(self, event_type: EventType | None, callback: Subscriber):
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
        """发射事件到所有匹配的订阅者（并发执行，带 1 秒超时）。

        慢订阅者不会阻塞发送方。超时的订阅者会被取消。
        """
        tasks: list[asyncio.Task] = []

        for cb in self._global_subscribers:
            tasks.append(
                asyncio.create_task(
                    self._safe_call(cb, event),
                    name="bus:global",
                )
            )
        for cb in self._subscribers.get(event.type, []):
            tasks.append(
                asyncio.create_task(
                    self._safe_call(cb, event),
                    name=f"bus:{event.type.value}",
                )
            )

        if tasks:
            # 等待最多 1 秒，避免慢订阅者阻塞发送方
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                log.debug(f"MessageBus: 订阅者处理超时，取消剩余任务")
                for task in tasks:
                    if not task.done():
                        task.cancel()
                # 等待取消完成，忽略 CancelledError
                await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _safe_call(cb: Subscriber, event: Event):
        try:
            await cb(event)
        except Exception:
            log.debug(f"MessageBus 订阅者异常: {event.type.value}", exc_info=True)


class NullBus(MessageBus):
    async def emit(self, event: Event):
        pass
