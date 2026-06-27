"""
SystemStats — service-level statistics tracking.

Pure dataclass: no external dependencies. Callers compose
store count / websocket clients / error stats separately.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


def _format_uptime(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


@dataclass
class SystemStats:
    crawl_requests: int = 0
    download_requests: int = 0
    api_calls: int = 0
    start_time: float = field(default_factory=time.time)
    # 刻意使用 threading.Lock 而非 asyncio.Lock：
    # 所有加锁操作均为同步 O(1) 整数递增，无 await 点，
    # threading.Lock 可同时兼容 asyncio 和原生线程场景。
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_crawl(self):
        with self._lock:
            self.crawl_requests += 1

    def record_download(self):
        with self._lock:
            self.download_requests += 1

    def record_api_call(self):
        with self._lock:
            self.api_calls += 1

    def as_dict(self) -> dict:
        with self._lock:
            uptime = time.time() - self.start_time
            return {
                "uptime_sec": round(uptime, 1),
                "uptime_human": _format_uptime(uptime),
                "crawl_requests": self.crawl_requests,
                "download_requests": self.download_requests,
                "api_calls": self.api_calls,
            }
