from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator


class TaskStatus(str, Enum):
    pending     = "pending"
    crawling    = "crawling"
    classifying = "classifying"
    adding      = "adding"
    queued      = "queued"
    downloading = "downloading"
    success     = "success"
    error       = "error"
    skipped     = "skipped"


class MagnetItem(BaseModel):
    hash:       str
    name:       str
    magnet:     str
    size:       Optional[str] = None
    source_url: str = ""
    category:   Optional[str] = None
    save_path:  Optional[str] = None
    status:     TaskStatus = TaskStatus.pending
    progress:   float = 0.0
    torrent_state: Optional[str] = None
    error_msg:  Optional[str] = None


class CrawlRequest(BaseModel):
    url:           str
    depth:         int  = 1
    auto_download: bool = False

    # Fix C: clamp depth to prevent exponential page explosion
    @field_validator("depth")
    @classmethod
    def clamp_depth(cls, v: int) -> int:
        return max(1, min(v, 3))


class DownloadRequest(BaseModel):
    hashes: List[str]


# ── Metrics 接口 — 统一指标快照 ──────────────────────────

@dataclass
class MetricSnapshot:
    """所有指标收集器的统一快照格式。

    每个适配器实现 snapshot() → MetricSnapshot，由 MetricsReport 合并。
    """
    namespace: str                          # e.g. "crawler", "classifier", "qbit"
    values: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"namespace": self.namespace, **self.values}
