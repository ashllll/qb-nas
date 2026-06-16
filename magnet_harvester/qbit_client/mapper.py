"""Map qBittorrent torrent states to the application's TaskStatus model."""
from __future__ import annotations

from magnet_harvester.models import TaskStatus


class TorrentStatusMapper:
    @staticmethod
    def map(torrent: dict) -> dict:
        state = str(torrent.get("state", "") or "")
        progress = float(torrent.get("progress") or 0.0)

        downloading_states = {
            "downloading",
            "forcedDL",
            "metaDL",
            "stalledDL",
            "checkingDL",
            "checkingResumeData",
            "moving",
            "pausedDL",  # qB queue management can temporarily pause active downloads.
            "queuedDL",  # Treat queue wait as downloading to avoid UI status oscillation.
        }
        success_states = {
            "uploading",
            "stalledUP",
            "forcedUP",
            "pausedUP",
            "checkingUP",
            "queuedUP",
        }
        error_states = {"error", "missingFiles", "unknown"}

        if state in error_states:
            status = TaskStatus.error
        elif progress >= 1.0 or state in success_states:
            status = TaskStatus.success
        elif state in downloading_states or 0.0 < progress < 1.0:
            status = TaskStatus.downloading
        else:
            # Unknown qB state — map to error to surface anomalies
            status = TaskStatus.error

        return {
            "status": status,
            "progress": round(progress * 100, 1),
            "torrent_state": state or None,
        }
