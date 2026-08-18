"""
测试 get_default_save_path 的 double-checked 并发探测去重。

PUT /api/config 清缓存后，并发下载任务会同时进入 get_default_save_path；
锁应保证底层 resolve 只执行一次，避免重复网络请求与重复日志。
"""

from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.qbit_client import QBittorrentClient


class _FakeResolver:
    def __init__(self, result: str | None):
        self._result = result
        self.calls = 0

    def clear_cache(self) -> None:
        pass

    async def resolve(self) -> str | None:
        self.calls += 1
        # 模拟真实网络探测耗时，放大竞态窗口
        await asyncio.sleep(0.01)
        return self._result


def _make_client(resolver):
    client = QBittorrentClient.__new__(QBittorrentClient)
    client._cached_default_path = None
    client._default_path_lock = asyncio.Lock()
    client._path_resolver = resolver
    return client


async def _run():
    resolver = _FakeResolver("/downloads")

    async def one(client):
        return await client.get_default_save_path()

    # 并发探测同一路径
    client = _make_client(resolver)
    results = await asyncio.gather(*[one(client) for _ in range(5)])
    assert results == ["/downloads"] * 5
    # double-checked locking：底层 resolve 只被调用一次
    assert resolver.calls == 1


def test_concurrent_default_save_path_resolves_once():
    asyncio.run(_run())


async def _run_negative_cache():
    resolver = _FakeResolver(None)

    client = _make_client(resolver)
    results = await asyncio.gather(*[client.get_default_save_path() for _ in range(3)])
    # 负缓存：所有调用返回 None，且只探测一次
    assert results == [None, None, None]
    assert resolver.calls == 1


def test_concurrent_default_save_path_negative_cache():
    asyncio.run(_run_negative_cache())


def test_clear_cached_path_resets_for_reprobe():
    async def _run():
        resolver = _FakeResolver("/downloads")
        client = _make_client(resolver)
        assert await client.get_default_save_path() == "/downloads"
        assert resolver.calls == 1
        client.clear_cached_path()
        assert client._cached_default_path is None
        assert await client.get_default_save_path() == "/downloads"
        assert resolver.calls == 2

    asyncio.run(_run())


if __name__ == "__main__":
    test_concurrent_default_save_path_resolves_once()
    test_concurrent_default_save_path_negative_cache()
    test_clear_cached_path_resets_for_reprobe()
    print("=== concurrent default save path tests passed! ===")
