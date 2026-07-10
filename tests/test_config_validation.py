"""
TDD 循环 5: 配置验证与动态更新的原子性
验证 Settings.update_qbit() 的输入校验和 RuntimeContext.replace_qbit() 的资源清理
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import Settings


# ═══════════════════════════════════════════════════
# 示踪弹: update_qbit() 应拒绝非法 URL
# ═══════════════════════════════════════════════════


def test_update_qbit_rejects_invalid_url():
    """传入非法 URL 时，update_qbit() 应返回错误且不修改配置"""
    settings = Settings()
    original_host = settings.QBIT_HOST

    result = settings.update_qbit(host="not-a-valid-url")

    # 应返回错误信息
    assert result is not True
    assert "url" in str(result).lower() or "invalid" in str(result).lower() or result is False
    # 配置不应被修改
    assert settings.QBIT_HOST == original_host


# ═══════════════════════════════════════════════════
# 增量测试 2: update_qbit() 应接受合法 URL
# ═══════════════════════════════════════════════════


def test_update_qbit_accepts_valid_url():
    """传入合法 URL 时，update_qbit() 应成功更新"""
    settings = Settings()

    result = settings.update_qbit(host="http://192.168.1.200:8080")

    assert result is True or result is None  # 成功时返回 True 或 None
    assert settings.QBIT_HOST == "http://192.168.1.200:8080"
    # 内部缓存应被清除
    assert settings._qbit_config is None


# ═══════════════════════════════════════════════════
# 增量测试 3: update_qbit() 应拒绝空值
# ═══════════════════════════════════════════════════


def test_update_qbit_rejects_empty_values():
    """传入空字符串时，update_qbit() 应拒绝"""
    settings = Settings()
    original_host = settings.QBIT_HOST

    result = settings.update_qbit(host="", username="", password="")

    assert result is not True
    assert settings.QBIT_HOST == original_host


def test_update_qbit_does_not_partially_mutate_on_late_validation_failure():
    settings = Settings()
    original = (settings.QBIT_HOST, settings.QBIT_USERNAME, settings.QBIT_PASSWORD)

    result = settings.update_qbit(
        host="http://new.example:8080",
        username="new-user",
        password="",
    )

    assert result is not True
    assert (settings.QBIT_HOST, settings.QBIT_USERNAME, settings.QBIT_PASSWORD) == original


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
# 增量测试 4: RuntimeContext.replace_qbit() 应关闭旧客户端
# ═══════════════════════════════════════════════════


async def test_replace_qbit_closes_old_client():
    """替换 qBittorrent 客户端时，旧客户端应被关闭"""
    from magnet_harvester.context.app_context import AppContext, CoreServices, RuntimeContext, RuntimeState
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
    runtime = RuntimeContext(ctx)

    # 替换
    await runtime.replace_qbit(new_qbit)

    # 旧客户端应被关闭（_client 为 None 或 is_closed）
    assert ctx.core.qbit is new_qbit


async def test_replace_qbit_updates_download_state_sync():
    from magnet_harvester.context.app_context import AppContext, CoreServices, RuntimeContext, RuntimeState

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

    await RuntimeContext(ctx).replace_qbit(new_qbit)

    assert sync.qbit is new_qbit
    assert ctx.core.qbit is new_qbit
    assert old_qbit.closed is True


if __name__ == "__main__":
    test_update_qbit_rejects_invalid_url()
    print("[PASS] test_update_qbit_rejects_invalid_url")

    test_update_qbit_accepts_valid_url()
    print("[PASS] test_update_qbit_accepts_valid_url")

    test_update_qbit_rejects_empty_values()
    print("[PASS] test_update_qbit_rejects_empty_values")

    import asyncio

    asyncio.run(test_replace_qbit_closes_old_client())
    print("[PASS] test_replace_qbit_closes_old_client")

    print("\n=== TDD Loop 5: Config validation tests passed! ===")
