"""qBittorrent client operation statistics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class QBittorrentStats:
    total_added: int = 0
    total_success: int = 0
    total_failed: int = 0
    consecutive_failures: int = 0
    last_success_time: Optional[float] = None
    last_failure_time: Optional[float] = None
    start_time: float = field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        if self.total_added == 0:
            return 0.0
        return self.total_success / self.total_added * 100

    def as_dict(self) -> dict:
        return {
            "total_added": self.total_added,
            "total_success": self.total_success,
            "total_failed": self.total_failed,
            "success_rate": round(self.success_rate, 1),
            "consecutive_failures": self.consecutive_failures,
            "last_success": (
                time.strftime("%H:%M:%S", time.localtime(self.last_success_time))
                if self.last_success_time
                else None
            ),
            "last_failure": (
                time.strftime("%H:%M:%S", time.localtime(self.last_failure_time))
                if self.last_failure_time
                else None
            ),
            "uptime_sec": round(time.time() - self.start_time, 1),
        }
