"""
测试 qB 状态到 TaskStatus 的映射
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import TaskStatus
from magnet_harvester.qbit_client import QBittorrentClient, TorrentStatusMapper


def test_map_torrent_status_for_queue_waiting_download():
    result = TorrentStatusMapper.map({"state": "queuedDL", "progress": 0.0})
    assert result["status"] == TaskStatus.downloading
    assert result["progress"] == 0.0


def test_map_torrent_status_for_paused_download():
    result = TorrentStatusMapper.map({"state": "pausedDL", "progress": 0.0})
    assert result["status"] == TaskStatus.downloading


def test_map_torrent_status_for_downloading():
    result = TorrentStatusMapper.map({"state": "downloading", "progress": 0.42})
    assert result["status"] == TaskStatus.downloading
    assert result["progress"] == 42.0


def test_map_torrent_status_for_completed():
    result = TorrentStatusMapper.map({"state": "uploading", "progress": 1.0})
    assert result["status"] == TaskStatus.success
    assert result["progress"] == 100.0


def test_map_torrent_status_for_completed_paused_upload():
    result = TorrentStatusMapper.map({"state": "pausedUP", "progress": 1.0})
    assert result["status"] == TaskStatus.success


def test_map_torrent_status_for_completed_queued_upload():
    result = TorrentStatusMapper.map({"state": "queuedUP", "progress": 1.0})
    assert result["status"] == TaskStatus.success


def test_map_torrent_status_for_error():
    result = TorrentStatusMapper.map({"state": "error", "progress": 0.0})
    assert result["status"] == TaskStatus.error
    assert result["error_msg"] == "qB 种子状态异常: error"


def test_map_torrent_status_missing_files_carries_readable_error():
    """missingFiles 必须透传为可读原因，前端据此提示用户检查文件而非等待重试。"""
    result = TorrentStatusMapper.map({"state": "missingFiles", "progress": 0.4})
    assert result["status"] == TaskStatus.error
    assert result["error_msg"] == "qB 种子状态异常: missingFiles"


def test_map_torrent_status_unrecognized_state_carries_error_msg():
    result = TorrentStatusMapper.map({"state": "", "progress": 0.0})
    assert result["status"] == TaskStatus.error
    assert result["error_msg"] == "qB 种子状态无法识别: 空"


def test_map_torrent_status_healthy_states_have_no_error_msg():
    for state, progress in [("downloading", 0.4), ("uploading", 1.0), ("queuedDL", 0.0)]:
        result = TorrentStatusMapper.map({"state": state, "progress": progress})
        assert result["error_msg"] is None


# qBittorrent 全部合法 torrent state。来源：qB WebAPI sync/torrentPeers 与
# torrents/info 文档；"空" 表示该字段缺失，属异常数据，不在本表内。
LEGITIMATE_QB_STATES = [
    "error",
    "missingFiles",
    "uploading",
    "pausedUP",
    "queuedUP",
    "stalledUP",
    "checkingUP",
    "forcedUP",
    "allocating",
    "downloading",
    "metaDL",
    "forcedMetaDL",
    "pausedDL",
    "queuedDL",
    "stalledDL",
    "checkingDL",
    "forcedDL",
    "checkingResumeData",
    "moving",
    "unknown",
    "stoppedDL",
    "stoppedUP",
]


def test_no_legitimate_qb_state_is_treated_as_unrecognized():
    """合法 qB 状态不得落进「无法识别」分支。

    回归背景：allocating / forcedMetaDL / stoppedDL / stoppedUP 不在映射表内，
    落进 else 被判 error。allocating 是大种子添加后的必然阶段（预分配空间），
    因此每添加一个大种子，2 秒后同步循环就会把条目写成 error 并推给前端。
    """
    for state in LEGITIMATE_QB_STATES:
        for progress in (0.0, 0.5, 1.0):
            result = TorrentStatusMapper.map({"state": state, "progress": progress})
            assert result["error_msg"] != f"qB 种子状态无法识别: {state}", (
                f"合法状态 {state!r}（progress={progress}）被判为无法识别"
            )


def test_allocating_and_forced_meta_dl_are_downloading():
    """预分配与强制元数据下载属进行中，不是错误。"""
    for state in ("allocating", "forcedMetaDL"):
        result = TorrentStatusMapper.map({"state": state, "progress": 0.0})
        assert result["status"] == TaskStatus.downloading, f"{state} 应为 downloading"
        assert result["error_msg"] is None


def test_stopped_states_mirror_paused_states():
    """qB 5.x 用 stoppedDL/stoppedUP 取代 pausedDL/pausedUP，语义需对齐。

    只比较映射结论（status/progress/error_msg）；torrent_state 有意保留 qB 原样
    状态串供前端展示，两者本就不同。
    """
    conclusion = ("status", "progress", "error_msg")
    for stopped, paused in (("stoppedDL", "pausedDL"), ("stoppedUP", "pausedUP")):
        for progress in (0.0, 0.4, 1.0):
            got = TorrentStatusMapper.map({"state": stopped, "progress": progress})
            want = TorrentStatusMapper.map({"state": paused, "progress": progress})
            assert {k: got[k] for k in conclusion} == {k: want[k] for k in conclusion}, (
                f"{stopped} 与 {paused} 在 progress={progress} 时映射结论不一致"
            )


def test_qbit_client_status_mapping_keeps_backward_compatibility():
    states = [
        ("queuedDL", 0.0),
        ("pausedDL", 0.0),
        ("downloading", 0.42),
        ("uploading", 1.0),
        ("error", 0.0),
    ]
    for state, progress in states:
        torrent = {"state": state, "progress": progress}
        assert QBittorrentClient.map_torrent_status(torrent) == TorrentStatusMapper.map(torrent)


if __name__ == "__main__":
    test_map_torrent_status_for_queue_waiting_download()
    test_map_torrent_status_for_paused_download()
    test_map_torrent_status_for_downloading()
    test_map_torrent_status_for_completed()
    test_map_torrent_status_for_completed_paused_upload()
    test_map_torrent_status_for_completed_queued_upload()
    test_map_torrent_status_for_error()
    test_qbit_client_status_mapping_keeps_backward_compatibility()
    print("=== qB state mapping tests passed! ===")
