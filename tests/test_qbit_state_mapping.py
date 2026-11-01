"""qB → TaskStatus 映射：v4（pausedDL/…）与 v5（stoppedDL/…）两套状态都要认。

N18：qBittorrent 5.2 把 ``pausedDL``/``pausedUP`` 改名为 ``stoppedDL``/``stoppedUP``，
旧映射表落到 else → error，用户库里 58 条种子全显示「失败」。
"""

import logging
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import TaskStatus
from magnet_harvester.qbit_client import QBittorrentClient, TorrentStatusMapper


def _map(state: str, progress: float = 0.0, **extra) -> dict:
    return TorrentStatusMapper.map({"state": state, "progress": progress, **extra})


# ── v4 与 v5：暂停/停止都是 paused，不是失败 ──────────────────


@pytest.mark.parametrize("state", ["pausedDL", "stoppedDL"])
def test_paused_download_states_map_to_paused(state):
    result = _map(state)
    assert result["status"] == TaskStatus.paused
    assert result["error_msg"] is None
    assert result["torrent_state"] == state


@pytest.mark.parametrize("state", ["pausedUP", "stoppedUP"])
def test_paused_upload_states_map_to_success(state):
    result = _map(state, progress=1.0)
    assert result["status"] == TaskStatus.success
    assert result["error_msg"] is None


def test_v4_and_v5_paused_states_are_equivalent():
    v4 = _map("pausedDL")
    v5 = _map("stoppedDL")
    assert (v4["status"], v4["error_msg"], v4["progress"]) == (
        v5["status"],
        v5["error_msg"],
        v5["progress"],
    )


def test_task_status_paused_value_is_stable():
    """paused 的值是接口：前端按字符串 paused 判定「已暂停」，不能改。"""
    assert TaskStatus.paused.value == "paused"


# ── 下载中的各档 ────────────────────────────────────────


@pytest.mark.parametrize(
    "state",
    [
        "downloading",
        "forcedDL",
        "metaDL",
        "forcedMetaDL",
        "stalledDL",
        "checkingDL",
        "checkingResumeData",
        "moving",
    ],
)
def test_downloading_states(state):
    result = _map(state, progress=0.5)
    assert result["status"] == TaskStatus.downloading
    assert result["error_msg"] is None


@pytest.mark.parametrize("state", ["queuedDL", "queuedForChecking", "allocating"])
def test_queued_states(state):
    result = _map(state)
    assert result["status"] == TaskStatus.queued
    assert result["error_msg"] is None


def test_meta_download_is_not_paused():
    """v5 的 forcedMetaDL 是「正在取元数据」，不是暂停。"""
    assert _map("forcedMetaDL")["status"] == TaskStatus.downloading


# ── 完成 ────────────────────────────────────────────────


def test_completed_by_progress():
    result = _map("uploading", progress=1.0)
    assert result["status"] == TaskStatus.success
    assert result["progress"] == 100.0


def test_completed_stalled_up_with_full_size():
    result = TorrentStatusMapper.map(
        {"state": "stalledUP", "progress": 0.9, "downloaded": 100, "total_size": 100}
    )
    assert result["status"] == TaskStatus.success


def test_up_state_without_data_is_not_success():
    """UP 档却一条数据都没有：更像卡住，不冒充成功（保持原有语义）。"""
    result = TorrentStatusMapper.map(
        {"state": "stalledUP", "progress": 0.0, "downloaded": 0, "total_size": 100}
    )
    assert result["status"] == TaskStatus.downloading


# ── 异常与未知 ───────────────────────────────────────────


@pytest.mark.parametrize("state", ["error", "missingFiles", "unknown"])
def test_error_states_carry_readable_reason(state):
    result = _map(state, progress=0.4)
    assert result["status"] == TaskStatus.error
    assert result["error_msg"] == f"qB 种子状态异常: {state}"


def test_empty_state_is_error_and_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="magnet_harvester.qbit_client.mapper"):
        result = TorrentStatusMapper.map({"state": "", "progress": 0.0})
    assert result["status"] == TaskStatus.error
    assert result["error_msg"] == "qB 种子状态无法识别: 空"


def test_unknown_state_keeps_raw_string_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="magnet_harvester.qbit_client.mapper"):
        result = _map("someFutureState")
    assert result["status"] == TaskStatus.error
    assert "someFutureState" in result["error_msg"]
    assert any("someFutureState" in record.getMessage() for record in caplog.records)


def test_state_matching_is_case_insensitive():
    """qB 的状态串大小写若变了，也不能从 paused 掉成 error。"""
    assert _map("StoppedDL")["status"] == TaskStatus.paused


def test_healthy_states_have_no_error_msg():
    for state, progress in [
        ("downloading", 0.4),
        ("uploading", 1.0),
        ("queuedDL", 0.0),
        ("stoppedDL", 0.0),
    ]:
        result = TorrentStatusMapper.map({"state": state, "progress": progress})
        assert result["error_msg"] is None


def test_qbit_client_status_mapping_keeps_backward_compatibility():
    states = [
        ("queuedDL", 0.0),
        ("pausedDL", 0.0),
        ("stoppedDL", 0.0),
        ("downloading", 0.42),
        ("uploading", 1.0),
        ("error", 0.0),
    ]
    for state, progress in states:
        torrent = {"state": state, "progress": progress}
        assert QBittorrentClient.map_torrent_status(torrent) == TorrentStatusMapper.map(torrent)
