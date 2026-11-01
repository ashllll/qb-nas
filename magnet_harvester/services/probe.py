"""带缓存与超时的健康探测。

菜单栏和页面会频繁问 ``/api/v1/health``、``/api/v1/status``；如果每次都真去 ping
qBittorrent，qB 离线时（NAS 现场就是离线）一次 ping 会重试十秒，接口跟着卡住。
这里把结果缓存一小段时间，并给单次探测一个短超时：读得快，又不会在 qB 恢复后一直
显示离线。
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable


class CachedProbe:
    """把一个「会慢、会失败」的探测包成有 TTL 的布尔值。"""

    def __init__(self, *, ttl: float = 10.0, timeout: float = 1.5):
        self._ttl = max(0.0, float(ttl))
        self._timeout = max(0.1, float(timeout))
        self._value = False
        self._checked_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def last_value(self) -> bool:
        return self._value

    async def read(self, probe: Callable[[], Awaitable[bool]]) -> bool:
        if self._fresh():
            return self._value
        async with self._lock:
            if self._fresh():
                return self._value
            try:
                value = await asyncio.wait_for(probe(), timeout=self._timeout)
            except (asyncio.TimeoutError, Exception):
                value = False
            self._value = bool(value)
            self._checked_at = time.monotonic()
            return self._value

    def _fresh(self) -> bool:
        return self._checked_at > 0 and (time.monotonic() - self._checked_at) < self._ttl
