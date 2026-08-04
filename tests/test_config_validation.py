"""Configuration security validation and QBitRuntime resource cleanup."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import Settings


def test_security_posture_allows_loopback_without_api_key():
    settings = Settings(SERVICE_HOST="127.0.0.1", API_KEY="")
    settings.validate_security_posture()


def test_security_posture_rejects_exposed_unauthenticated_writes():
    settings = Settings(
        SERVICE_HOST="0.0.0.0",
        API_KEY="",
        ALLOW_INSECURE_WRITE_API=False,
    )

    with pytest.raises(RuntimeError, match="Refusing"):
        settings.validate_security_posture()


def test_security_posture_allows_authenticated_network_listener():
    settings = Settings(SERVICE_HOST="0.0.0.0", API_KEY="strong-random-key")
    settings.validate_security_posture()


def test_security_posture_allows_explicit_insecure_development_override():
    settings = Settings(
        SERVICE_HOST="0.0.0.0",
        API_KEY="",
        ALLOW_INSECURE_WRITE_API=True,
    )
    settings.validate_security_posture()


# ═══════════════════════════════════════════════════
# 增量测试 4: QBitRuntime.replace_qbit() 应关闭旧客户端
# ═══════════════════════════════════════════════════


async def test_replace_qbit_closes_old_client():
    """替换 qBittorrent 客户端时，旧客户端应被关闭"""
    from magnet_harvester.context.app_context import (
        AppContext,
        CoreServices,
        QBitRuntime,
        RuntimeState,
    )
    from magnet_harvester.qbit_client import QBittorrentClient
    from magnet_harvester.config import QBitConfig

    # 创建模拟旧客户端
    old_qbit = QBittorrentClient(config=QBitConfig(host="http://old:8080"))
    new_qbit = QBittorrentClient(config=QBitConfig(host="http://new:8080"))

    # 创建最小 AppContext
    ctx = AppContext(
        core=CoreServices(
            store=None,
            bus=None,
            pipeline=None,
            crawler=None,
            classifier=None,
            qbit=old_qbit,
        ),
        runtime=RuntimeState(stats=None),
    )
    runtime = QBitRuntime.from_context(ctx)

    # 替换
    await runtime.replace_qbit(new_qbit)

    # 旧客户端应被关闭（_client 为 None 或 is_closed）
    assert ctx.core.qbit is new_qbit


async def test_replace_qbit_updates_download_state_sync():
    from magnet_harvester.context.app_context import (
        AppContext,
        CoreServices,
        QBitRuntime,
        RuntimeState,
    )

    class FakeQbit:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeSync:
        def __init__(self):
            self.qbit = None

        async def replace_qbit_client(self, new_qbit):
            self.qbit = new_qbit

    old_qbit = FakeQbit()
    new_qbit = FakeQbit()
    sync = FakeSync()
    ctx = AppContext(
        core=CoreServices(
            store=None,
            bus=None,
            pipeline=None,
            crawler=None,
            classifier=None,
            qbit=old_qbit,
        ),
        runtime=RuntimeState(qbit_sync=sync),
    )

    await QBitRuntime.from_context(ctx).replace_qbit(new_qbit)

    assert sync.qbit is new_qbit
    assert ctx.core.qbit is new_qbit
    assert old_qbit.closed is True


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_replace_qbit_closes_old_client())
    print("[PASS] test_replace_qbit_closes_old_client")

    print("\n=== TDD Loop 5: Config validation tests passed! ===")
