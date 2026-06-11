"""TorrentStatusMapper — qBittorrent 状态到 TaskStatus 的映射"""
from __future__ import annotations

from magnet_harvester.models import TaskStatus


class TorrentStatusMapper:
    @staticmethod
    def map(torrent: dict) -> dict:
        state = str(torrent.get("state", "") or "")
        progress = float(torrent.get("progress") or 0.0)

        queued_states = {"queuedDL", "pausedDL"}
        downloading_states = {
            "downloading", "forcedDL", "metaDL", "stalledDL",
            "checkingDL", "checkingResumeData", "moving",
        }
        success_states = {
            "uploading", "stalledUP", "forcedUP", "pausedUP", "checkingUP", "queuedUP",
        }
        error_states = {"error", "missingFiles", "unknown"}

        if state in error_states:
            status = TaskStatus.error
        elif progress >= 1.0 or state in success_states:
            status = TaskStatus.success
        elif state in downloading_states or 0.0 < progress < 1.0:
            status = TaskStatus.downloading
        elif state in queued_states:
            status = TaskStatus.queued
        else:
            status = TaskStatus.queued

        return {
            "status": status,
            "progress": round(progress * 100, 1),
            "torrent_state": state or None,
        }
