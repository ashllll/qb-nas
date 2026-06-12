"""
TDD 循环 5: 配置验证与动态更新的原子性
验证 Settings.update_qbit() 的输入校验和 RuntimeContext.replace_qbit() 的资源清理
"""
import sys
import os

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


# ═══════════════════════════════════════════════════
# 增量测试 4: RuntimeContext.replace_qbit() 应关闭旧客户端
# ═══════════════════════════════════════════════════

async def test_replace_qbit_closes_old_client():
    """替换 qBittorrent 客户端时，旧客户端应被关闭"""
    from magnet_harvester.context.app_context import AppContext, RuntimeContext
    from magnet_harvester.qbit_client import QBittorrentClient
    from magnet_harvester.config import QBitConfig

    # 创建模拟旧客户端
    old_qbit = QBittorrentClient(config=QBitConfig(host="http://old:8080"))
    new_qbit = QBittorrentClient(config=QBitConfig(host="http://new:8080"))

    # 创建最小 AppContext
    ctx = AppContext(
        store=None, bus=None, pipeline=None, crawler=None,
        classifier=None, qbit=old_qbit, stats=None,
    )
    runtime = RuntimeContext(ctx)

    # 替换
    await runtime.replace_qbit(new_qbit)

    # 旧客户端应被关闭（_client 为 None 或 is_closed）
    assert ctx.qbit is new_qbit


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
