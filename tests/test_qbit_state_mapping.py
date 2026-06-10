"""
测试 qB 状态到 TaskStatus 的映射
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.models import TaskStatus
from magnet_harvester.qbit_client import QBittorrentClient


def test_map_torrent_status_for_queue():
    result = QBittorrentClient.map_torrent_status({"state": "queuedDL", "progress": 0.0})
    assert result["status"] == TaskStatus.queued
    assert result["progress"] == 0.0


def test_map_torrent_status_for_downloading():
    result = QBittorrentClient.map_torrent_status({"state": "downloading", "progress": 0.42})
    assert result["status"] == TaskStatus.downloading
    assert result["progress"] == 42.0


def test_map_torrent_status_for_completed():
    result = QBittorrentClient.map_torrent_status({"state": "uploading", "progress": 1.0})
    assert result["status"] == TaskStatus.success
    assert result["progress"] == 100.0


def test_map_torrent_status_for_error():
    result = QBittorrentClient.map_torrent_status({"state": "error", "progress": 0.0})
    assert result["status"] == TaskStatus.error


if __name__ == "__main__":
    test_map_torrent_status_for_queue()
    test_map_torrent_status_for_downloading()
    test_map_torrent_status_for_completed()
    test_map_torrent_status_for_error()
    print("=== qB state mapping tests passed! ===")
