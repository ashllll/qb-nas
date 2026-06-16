"""Internal HTTP transport for qBittorrent WebAPI v2.

Handles session management, login, and retries. This is an implementation detail
of ``QBittorrentClient``; external code should interact with the client itself.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

log = logging.getLogger(__name__)


class QBitTransport:
    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        stats,
    ):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self._stats = stats
        self._cookie = None
        self._client: Optional[httpx.AsyncClient] = None
        self._retry_config = {
            "max_retries": 3,
            "base_delay": 1.0,
            "max_delay": 10.0,
            "retry_on": [408, 429, 500, 502, 503, 504],
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
            self._client = httpx.AsyncClient(
                limits=limits,
                timeout=httpx.Timeout(connect=3, read=30, write=30, pool=30),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.cookies.clear()
            await self._client.aclose()
            self._client = None
        self._cookie = None

    async def _login(self, force: bool = False) -> bool:
        if not force and self._cookie:
            return True

        try:
            client = await self._get_client()
            if force:
                client.cookies.clear()
            r = await client.post(
                f"{self.host}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
            )

            if r.text.strip() == "Ok.":
                client.cookies = r.cookies
                self._cookie = client.cookies
                log.info("qBittorrent 登录成功")
                return True

            log.error(f"qBittorrent 登录失败: {r.text[:100]}")
            self._stats.consecutive_failures += 1
            self._stats.last_failure_time = time.time()
            return False

        except Exception as e:
            log.error(f"qBittorrent 登录异常: {e}")
            self._stats.consecutive_failures += 1
            self._stats.last_failure_time = time.time()
            return False

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        config = self._retry_config
        last_exception = None
        auth_retry_count = 0
        max_auth_retries = 2

        for attempt in range(config["max_retries"]):
            try:
                if not self._cookie:
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
                    if auth_retry_count >= max_auth_retries:
                        raise RuntimeError(
                            f"qBittorrent Session 过期（已重试{max_auth_retries}次）"
                        )

                    log.warning("qBittorrent Session 过期，重新登录...")
                    self._cookie = None
                    client.cookies.clear()
                    auth_retry_count += 1
                    ok = await self._login(force=True)
                    if not ok:
                        raise RuntimeError("qBittorrent 重新登录失败")
                    continue

                if r.status_code in config["retry_on"] and attempt < config["max_retries"] - 1:
                    delay = min(config["base_delay"] * (2 ** attempt), config["max_delay"])
                    log.warning(f"qBittorrent 请求失败 ({r.status_code})，{delay:.1f}秒后重试...")
                    await asyncio.sleep(delay)
                    continue

                return r

            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < config["max_retries"] - 1:
                    delay = min(config["base_delay"] * (2 ** attempt), config["max_delay"])
                    log.warning(f"qBittorrent 请求超时，{delay:.1f}秒后重试...")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"qBittorrent 请求超时（已重试{config['max_retries']}次）")

            except httpx.ConnectError as e:
                last_exception = e
                if attempt < config["max_retries"] - 1:
                    delay = min(config["base_delay"] * (2 ** attempt), config["max_delay"])
                    log.warning(f"qBittorrent 连接失败，{delay:.1f}秒后重试...")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"qBittorrent 连接失败（已重试{config['max_retries']}次）")

            except Exception as e:
                last_exception = e
                log.error(f"qBittorrent 请求异常: {e}")
                break

        raise last_exception or RuntimeError("qBittorrent 请求失败")
