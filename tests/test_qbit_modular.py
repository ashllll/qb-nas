"""
TDD 循环 2: qBittorrent 客户端模块化拆分
验证提取后的子模块保持原有行为
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import TaskStatus


# ═══════════════════════════════════════════════════
# 示踪弹 1: TorrentStatusMapper 可独立测试
# ═══════════════════════════════════════════════════

def test_status_mapper_maps_error_states():
    """错误状态应映射为 TaskStatus.error"""
    from magnet_harvester.qbit_client import TorrentStatusMapper

    mapper = TorrentStatusMapper()
    for state in ["error", "missingFiles", "unknown"]:
        result = mapper.map({"state": state, "progress": 0.5})
        assert result["status"] == TaskStatus.error


def test_status_mapper_maps_success_states():
    """完成/做种状态应映射为 TaskStatus.success"""
    from magnet_harvester.qbit_client import TorrentStatusMapper

    mapper = TorrentStatusMapper()
    for state in ["uploading", "stalledUP", "forcedUP", "pausedUP", "checkingUP", "queuedUP"]:
        result = mapper.map({"state": state, "progress": 1.0})
        assert result["status"] == TaskStatus.success


def test_status_mapper_maps_downloading_states():
    """下载中状态应映射为 TaskStatus.downloading"""
    from magnet_harvester.qbit_client import TorrentStatusMapper

    mapper = TorrentStatusMapper()
    for state in ["downloading", "forcedDL", "metaDL", "stalledDL", "checkingDL", "checkingResumeData", "moving"]:
        result = mapper.map({"state": state, "progress": 0.5})
        assert result["status"] == TaskStatus.downloading


def test_status_mapper_maps_download_queue_states():
    """qB 下载队列状态应保持为下载中，避免 UI 状态震荡"""
    from magnet_harvester.qbit_client import TorrentStatusMapper

    mapper = TorrentStatusMapper()
    for state in ["queuedDL", "pausedDL"]:
        result = mapper.map({"state": state, "progress": 0.0})
        assert result["status"] == TaskStatus.downloading


def test_status_mapper_progress_rounding():
    """progress 应转换为百分比并保留 1 位小数"""
    from magnet_harvester.qbit_client import TorrentStatusMapper

    mapper = TorrentStatusMapper()
    result = mapper.map({"state": "downloading", "progress": 0.4567})
    assert result["progress"] == 45.7
    assert result["torrent_state"] == "downloading"


# ═══════════════════════════════════════════════════
# 增量测试 2: _safe_fs_segment 可独立测试
# ═══════════════════════════════════════════════════

def test_safe_fs_segment_sanitizes_path_traversal():
    """_safe_fs_segment 应移除路径穿越字符"""
    from magnet_harvester.qbit_client import _safe_fs_segment

    assert _safe_fs_segment("电影/电视剧") == "电影_电视剧"
    # \\ 被替换为 _，然后 strip('.') 去掉开头的 ..
    assert _safe_fs_segment("..\\\\上级目录") == "_上级目录"
    assert _safe_fs_segment("a:b\0c") == "a_b_c"


def test_safe_fs_segment_trims_dots_and_spaces():
    """_safe_fs_segment 应修剪首尾点和空格"""
    from magnet_harvester.qbit_client import _safe_fs_segment

    assert _safe_fs_segment("  电影  ") == "电影"
    assert _safe_fs_segment("...电影...") == "电影"
    assert _safe_fs_segment("   ") == "uncategorized"


# ═══════════════════════════════════════════════════
# 增量测试 3: QBittorrentStats 可独立测试
# ═══════════════════════════════════════════════════

def test_stats_tracks_success_and_failure():
    """QBittorrentStats 应正确追踪成功/失败计数"""
    from magnet_harvester.qbit_client import QBittorrentStats

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
    from magnet_harvester.qbit_client import QBittorrentStats

    stats = QBittorrentStats()
    assert stats.success_rate == 0.0


def test_qbit_ping_uses_short_cache():
    """连续状态轮询应复用短缓存，避免 qB 离线时反复慢连接"""
    import asyncio

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


# ═══════════════════════════════════════════════════
# 增量测试 4: 路径解析逻辑可独立测试（提取后）
# ═══════════════════════════════════════════════════

def test_extract_base_path_from_category():
    """从分类 savePath 提取基础路径"""
    # 这个测试将在提取路径解析模块后启用
    # 当前作为占位，验证提取后的行为
    pass


if __name__ == "__main__":
    test_status_mapper_maps_error_states()
    print("[PASS] test_status_mapper_maps_error_states")

    test_status_mapper_maps_success_states()
    print("[PASS] test_status_mapper_maps_success_states")

    test_status_mapper_maps_downloading_states()
    print("[PASS] test_status_mapper_maps_downloading_states")

    test_status_mapper_maps_download_queue_states()
    print("[PASS] test_status_mapper_maps_queued_states")

    test_status_mapper_progress_rounding()
    print("[PASS] test_status_mapper_progress_rounding")

    test_safe_fs_segment_sanitizes_path_traversal()
    print("[PASS] test_safe_fs_segment_sanitizes_path_traversal")

    test_safe_fs_segment_trims_dots_and_spaces()
    print("[PASS] test_safe_fs_segment_trims_dots_and_spaces")

    test_stats_tracks_success_and_failure()
    print("[PASS] test_stats_tracks_success_and_failure")

    test_stats_zero_division()
    print("[PASS] test_stats_zero_division")

    print("\n=== TDD Loop 2 (Phase 1): Pure logic extraction tests passed! ===")
