"""QBittorrentStats 与 ping 短缓存：从 test_qbit_modular.py 迁出的独有覆盖。"""

from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.qbit_client import QBittorrentStats


def test_stats_tracks_success_and_failure():
    """QBittorrentStats 应正确追踪成功/失败计数"""
    stats = QBittorrentStats()
    stats.total_added = 10
    stats.total_success = 7
    stats.total_failed = 3

    assert stats.success_rate == 70.0
    d = stats.as_dict()
    assert d["total_added"] == 10
    assert d["total_success"] == 7
    assert d["success_rate"] == 70.0


def test_stats_zero_division():
    """无添加时 success_rate 应为 0.0"""
    stats = QBittorrentStats()
    assert stats.success_rate == 0.0


def test_stats_records_submission_outcomes_and_last_error():
    stats = QBittorrentStats()
    stats.attempted()
    stats.failed()
    stats.error("qB rejected")
    stats.attempted()
    stats.succeeded()
    stats.error(None)

    assert stats.total_added == 2
    assert stats.total_success == 1
    assert stats.total_failed == 1
    assert stats.consecutive_failures == 0
    assert stats.last_error is None
    assert stats.last_success_time is not None
    assert stats.last_failure_time is not None


def test_qbit_ping_uses_short_cache():
    """连续状态轮询应复用短缓存，避免 qB 离线时反复慢连接"""
    from magnet_harvester.config import QBitConfig
    from magnet_harvester.qbit_client import QBittorrentClient

    class FakeResponse:
        status_code = 200

    client = QBittorrentClient(config=QBitConfig(host="http://qbit.example:8080"))
    calls = 0

    async def fake_req(_method, _path):
        nonlocal calls
        calls += 1
        return FakeResponse()

    client._req = fake_req

    async def run():
        assert await client.ping() is True
        assert await client.ping() is True

    asyncio.run(run())
    assert calls == 1
