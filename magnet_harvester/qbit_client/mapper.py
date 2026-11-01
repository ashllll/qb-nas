"""Map qBittorrent torrent states to the application's TaskStatus model.

qBittorrent 5.x 改了一批状态名（``pausedDL``/``pausedUP`` → ``stoppedDL``/
``stoppedUP``），两个大版本的状态串都必须认。映射规则见《约定》第 3 条：

- ``stoppedDL`` / ``pausedDL`` → ``paused``（已加入 qB、处于停止状态，属成功路径）
- ``stoppedUP`` / ``pausedUP``   → ``success``（UP 档意味着内容已完整）
- ``metaDL`` / ``forcedMetaDL``   → ``downloading``（正在取元数据）
- ``queuedForChecking`` / ``allocating`` / ``queuedDL`` → ``queued``
- ``error`` / ``missingFiles`` / ``unknown`` → ``error``，带原始状态串
- 认不出来的状态：保留 ``error``，但必须带上原始状态串并打一行 WARNING，
  不许静默、也不许冒充成「已暂停」。
"""

from __future__ import annotations

import logging

from magnet_harvester.models import TaskStatus

log = logging.getLogger(__name__)

# 状态名按 qB 的 camelCase 原样登记；比对时统一 lower()，避免大小写差异漏判。
PAUSED_STATES = frozenset({"stoppeddl", "pauseddl"})
SUCCESS_STATES = frozenset(
    {
        "uploading",
        "stalledup",
        "forcedup",
        "pausedup",
        "stoppedup",
        "checkingup",
        "queuedup",
    }
)
QUEUED_STATES = frozenset({"queueddl", "queuedforchecking", "allocating"})
DOWNLOADING_STATES = frozenset(
    {
        "downloading",
        "forceddl",
        "metadl",
        "forcedmetadl",
        "stalleddl",
        "checkingdl",
        "checkingresumedata",
        "moving",
    }
)
ERROR_STATES = frozenset({"error", "missingfiles", "unknown"})


class TorrentStatusMapper:
    @staticmethod
    def map(torrent: dict) -> dict:
        raw_state = str(torrent.get("state", "") or "")
        state = raw_state.strip().lower()
        progress = float(torrent.get("progress") or 0.0)
        downloaded = float(torrent.get("downloaded") or 0)
        total_size = float(torrent.get("total_size") or 0)

        # qB 侧异常状态时给出可读原因，供前端区分「文件缺失」与「状态未知」，
        # 避免笼统的「暂时异常」误导（同步层只报告状态，不存在自动重试）
        error_msg: str | None = None
        if state in ERROR_STATES:
            status = TaskStatus.error
            error_msg = f"qB 种子状态异常: {raw_state}"
        elif progress >= 1.0:
            # 内容已完整：无论 qB 给它挂的是 UP、暂停还是停止，都算成功
            status = TaskStatus.success
        elif state in PAUSED_STATES:
            # 「已暂停 / 已停止」不是失败：种子在 qB 里，只是没在跑
            status = TaskStatus.paused
        elif state in SUCCESS_STATES:
            if downloaded > 0 and total_size > 0 and downloaded >= total_size:
                # stalledUP / forcedUP with complete data -> success
                status = TaskStatus.success
            elif state in {"stalledup", "pausedup", "stoppedup"}:
                # UP 档却没有任何已完成数据：更像「没种子卡住了」，不冒充成功
                status = TaskStatus.downloading
            else:
                status = TaskStatus.success
        elif state in QUEUED_STATES:
            status = TaskStatus.queued
        elif state in DOWNLOADING_STATES or 0.0 < progress < 1.0:
            status = TaskStatus.downloading
        else:
            status = TaskStatus.error
            error_msg = f"qB 种子状态无法识别: {raw_state or '空'}"
            log.warning("qB 种子状态无法识别，按失败处理（原始状态=%r）", raw_state or "")

        return {
            "status": status,
            "progress": round(progress * 100, 1),
            "torrent_state": raw_state or None,
            "error_msg": error_msg,
        }
