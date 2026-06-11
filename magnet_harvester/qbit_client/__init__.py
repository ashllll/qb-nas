"""qbit_client package — qBittorrent WebAPI v2 client components.

向后兼容导出：旧代码可直接从 magnet_harvester.qbit_client 导入。
"""
from __future__ import annotations

from magnet_harvester.qbit_client.client import QBittorrentClient
from magnet_harvester.qbit_client.mapper import TorrentStatusMapper
from magnet_harvester.qbit_client.paths import QBitPathResolver, _safe_fs_segment
from magnet_harvester.qbit_client.stats import QBittorrentStats

__all__ = [
    "QBittorrentClient",
    "TorrentStatusMapper",
    "QBitPathResolver",
    "QBittorrentStats",
    "_safe_fs_segment",
]
