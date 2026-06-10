"""
测试 qB 状态到 TaskStatus 的映射
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import TaskStatus
from magnet_harvester.qbit_client import QBittorrentClient, TorrentStatusMapper


def test_map_torrent_status_for_queue():
    result = TorrentStatusMapper.map({"state": "queuedDL", "progress": 0.0})
    assert result["status"] == TaskStatus.queued
    assert result["progress"] == 0.0


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


def test_qbit_client_status_mapping_keeps_backward_compatibility():
    result = QBittorrentClient.map_torrent_status({"state": "uploading", "progress": 1.0})
    assert result == TorrentStatusMapper.map({"state": "uploading", "progress": 1.0})


if __name__ == "__main__":
    test_map_torrent_status_for_queue()
    test_map_torrent_status_for_downloading()
    test_map_torrent_status_for_completed()
    test_map_torrent_status_for_completed_paused_upload()
    test_map_torrent_status_for_completed_queued_upload()
    test_map_torrent_status_for_error()
    test_qbit_client_status_mapping_keeps_backward_compatibility()
    print("=== qB state mapping tests passed! ===")
