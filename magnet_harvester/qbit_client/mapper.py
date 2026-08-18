"""Map qBittorrent torrent states to the application's TaskStatus model."""

from __future__ import annotations

from magnet_harvester.models import TaskStatus


class TorrentStatusMapper:
    @staticmethod
    def map(torrent: dict) -> dict:
        state = str(torrent.get("state", "") or "")
        progress = float(torrent.get("progress") or 0.0)
        downloaded = float(torrent.get("downloaded") or 0)
        total_size = float(torrent.get("total_size") or 0)

        downloading_states = {
            "downloading",
            "forcedDL",
            "metaDL",
            "stalledDL",
            "checkingDL",
            "checkingResumeData",
            "moving",
            "pausedDL",
            "queuedDL",
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

        # qB 侧异常状态时给出可读原因，供前端区分「文件缺失」与「状态未知」，
        # 避免笼统的「暂时异常」误导（同步层只报告状态，不存在自动重试）
        error_msg: str | None = None
        if state in error_states:
            status = TaskStatus.error
            error_msg = f"qB 种子状态异常: {state}"
        elif progress >= 1.0:
            status = TaskStatus.success
        elif (
            state in success_states
            and downloaded > 0
            and total_size > 0
            and downloaded >= total_size
        ):
            # stalledUP / forcedUP with complete data -> success
            status = TaskStatus.success
        elif state in success_states:
            # stalledUP / pausedUP with zero progress -> no seeders; treat as stalled, not success
            status = TaskStatus.downloading
        elif state in downloading_states or 0.0 < progress < 1.0:
            status = TaskStatus.downloading
        else:
            status = TaskStatus.error
            error_msg = f"qB 种子状态无法识别: {state or '空'}"

        return {
            "status": status,
            "progress": round(progress * 100, 1),
            "torrent_state": state or None,
            "error_msg": error_msg,
        }
