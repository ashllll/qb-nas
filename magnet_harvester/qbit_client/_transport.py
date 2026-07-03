"""Internal HTTP transport for qBittorrent WebAPI v2.

Handles session management, login, and retries. This is an implementation detail
of ``QBittorrentClient``; external code should interact with the client itself.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class AsyncHttpClientLike(Protocol):
    is_closed: bool
    cookies: httpx.Cookies

    async def post(self, url: str, **kw) -> httpx.Response: ...
    async def request(self, method: str, url: str, **kw) -> httpx.Response: ...
    async def aclose(self) -> None: ...


HttpClientFactory = Callable[[], AsyncHttpClientLike]


class QBitTransport:
    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        stats,
        client_factory: HttpClientFactory | None = None,
    ):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self._stats = stats
        self._authenticated: bool = False
        self._client: AsyncHttpClientLike | None = None
        self._client_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._closing = asyncio.Event()
        self._client_factory = client_factory or self._build_client
        self._max_auth_retries = 2
        self._retry_config = {
            "max_retries": 3,
            "base_delay": 1.0,
            "max_delay": 10.0,
            "retry_on": [408, 429, 500, 502, 503, 504],
        }

    @staticmethod
    def _build_client() -> AsyncHttpClientLike:
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        return httpx.AsyncClient(
            limits=limits,
            timeout=httpx.Timeout(connect=3, read=30, write=30, pool=30),
        )

    async def _get_client(self) -> AsyncHttpClientLike:
        if self._closing.is_set():
            raise RuntimeError("transport is closing")
        if self._client is None or self._client.is_closed:
            async with self._client_lock:
                # 双重检查：获取锁后再次确认是否需要创建
                if self._client is None or self._client.is_closed:
                    self._client = self._client_factory()
                    self._authenticated = False  # 新 client，强制重新登录
        return self._client

    async def close(self) -> None:
        self._closing.set()
        if self._client and not self._client.is_closed:
            try:
                self._client.cookies.clear()
                await self._client.aclose()
            except Exception:
                log.exception("关闭 HTTP 客户端时出错")
            finally:
                self._client = None
        self._authenticated = False

    def _record_success(self) -> None:
        self._stats.consecutive_failures = 0
        self._stats.last_success_time = time.monotonic()

    def _record_failure(self) -> None:
        self._stats.consecutive_failures += 1
        self._stats.last_failure_time = time.monotonic()

    async def _login(self, force: bool = False) -> bool:
        async with self._auth_lock:
            if not force and self._authenticated:
                return True

            if force:
                self._authenticated = False

            last_error = None
            for attempt in range(self._max_auth_retries + 1):
                try:
                    client = await self._get_client()
                    if force or attempt > 0:
                        client.cookies.clear()
                    r = await client.post(
                        f"{self.host}/api/v2/auth/login",
                        data={"username": self.username, "password": self.password},
                    )

                    if r.text.strip() == "Ok.":
                        client.cookies.update(r.cookies)
                        self._authenticated = True
                        self._record_success()
                        log.info("qBittorrent 登录成功")
                        return True

                    # 认证凭据错误 — 不重试，立即失败
                    log.error("qBittorrent 登录失败: %s", r.text[:100])
                    self._record_failure()
                    return False

                except RuntimeError:
                    raise
                except Exception as e:
                    last_error = e
                    if attempt < self._max_auth_retries:
                        delay = self._backoff_delay(attempt)
                        log.warning(
                            "qBittorrent 登录网络异常，%.1f秒后重试(%d/%d): %s",
                            delay, attempt + 1, self._max_auth_retries, e,
                        )
                        await asyncio.sleep(delay)
                    else:
                        log.error(
                            "qBittorrent 登录异常（已重试%d次）: %s",
                            self._max_auth_retries, e,
                        )

            self._record_failure()
            return False

    async def _handle_auth_retry(self, client, auth_retry_count: int, max_auth_retries: int) -> int:
        """Handle 403 response by re-authenticating. Returns new retry count or raises."""
        if auth_retry_count >= max_auth_retries:
            raise RuntimeError(
                f"qBittorrent Session 过期（已重试{max_auth_retries}次）"
            )
        log.warning("qBittorrent Session 过期，重新登录...")
        count = auth_retry_count + 1
        ok = await self._login(force=True)
        if not ok:
            raise RuntimeError("qBittorrent 重新登录失败")
        return count

    async def _handle_network_retry(
        self, attempt: int, label: str, exc: Exception
    ) -> None:
        """Sleep with backoff on network errors if more retries remain; log otherwise."""
        if attempt < self._retry_config["max_retries"] - 1:
            delay = self._backoff_delay(attempt)
            log.warning("qBittorrent %s，%.1f秒后重试...", label, delay)
            await asyncio.sleep(delay)
        else:
            log.error("qBittorrent %s（已重试%d次）", label, self._retry_config["max_retries"])

    def _backoff_delay(self, attempt: int) -> float:
        """指数退避延迟，带随机抖动防止惊群。"""
        cfg = self._retry_config
        base = min(cfg["max_delay"], cfg["base_delay"] * (2**attempt))
        return base * (0.5 + random.random())

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        config = self._retry_config
        last_exception = None
        auth_retry_count = 0
        max_auth_retries = self._max_auth_retries

        for attempt in range(config["max_retries"]):
            try:
                if not self._authenticated:
                    ok = await self._login()
                    if not ok:
                        raise RuntimeError("qBittorrent 登录失败")

                client = await self._get_client()

                r = await client.request(
                    method,
                    f"{self.host}/api/v2{path}",
                    **kw,
                )

                if r.status_code == 403:
                    auth_retry_count = await self._handle_auth_retry(
                        client, auth_retry_count, max_auth_retries
                    )
                    continue

                if r.status_code in config["retry_on"]:
                    last_exception = RuntimeError(f"qBittorrent HTTP {r.status_code}")
                    if attempt < config["max_retries"] - 1:
                        delay = self._backoff_delay(attempt)
                        log.warning(f"qBittorrent 请求失败 ({r.status_code})，{delay:.1f}秒后重试...")
                        await asyncio.sleep(delay)
                        continue
                    # 最后一次重试仍失败，跳出由底部统一记录+抛出
                    break

                if r.status_code == 200:
                    self._record_success()
                    return r

                last_exception = RuntimeError(f"qBittorrent HTTP {r.status_code}: {r.text[:200]}")
                break

            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.RemoteProtocolError, httpx.ReadError,
                    httpx.WriteError, httpx.PoolTimeout) as e:
                last_exception = e
                await self._handle_network_retry(attempt, "传输异常", e)

            except RuntimeError:
                raise
            except Exception as e:
                last_exception = e
                log.error(f"qBittorrent 请求异常: {e}")
                break

        if last_exception is not None:
            self._record_failure()
            raise last_exception
        raise RuntimeError("qBittorrent 请求失败")
