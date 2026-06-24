from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from magnet_harvester.utils.url_validator import URLValidationError, validate_crawl_url


class TaskStatus(str, Enum):
    pending = "pending"
    crawling = "crawling"
    classifying = "classifying"
    adding = "adding"
    queued = "queued"
    downloading = "downloading"
    success = "success"
    error = "error"
    skipped = "skipped"


class MagnetItem(BaseModel):
    hash: str
    name: str
    magnet: str
    size: Optional[str] = None
    source_url: str = ""
    category: Optional[str] = None
    save_path: Optional[str] = None
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    torrent_state: Optional[str] = None
    error_msg: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @field_validator("progress")
    @classmethod
    def clamp_progress(cls, v: float) -> float:
        """确保进度值在 0.0–100.0 范围内。"""
        return max(0.0, min(float(v), 100.0))


class CrawlRequest(BaseModel):
    url: str
    depth: int = 1
    auto_download: bool = False

    # Fix C: clamp depth to prevent exponential page explosion
    @field_validator("depth")
    @classmethod
    def clamp_depth(cls, v: int) -> int:
        return max(1, min(v, 3))

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        try:
            validate_crawl_url(v)
        except URLValidationError as e:
            raise ValueError(str(e))
        return v


class DownloadRequest(BaseModel):
    hashes: List[str]


class QBitConfigUpdate(BaseModel):
    """qBittorrent 配置更新请求体，替换 routes.py 中的裸 dict。

    所有字段可选：仅更新显式传入的字段。
    """
    qbit_host: Optional[str] = None
    qbit_username: Optional[str] = None
    qbit_password: Optional[str] = None


# ── Metrics 接口 — 统一指标快照 ──────────────────────────


@dataclass
class MetricSnapshot:
    """所有指标收集器的统一快照格式。

    每个适配器实现 snapshot() → MetricSnapshot，由 MetricsReport 合并。
    """

    namespace: str  # e.g. "crawler", "classifier", "qbit"
    values: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"namespace": self.namespace, **self.values}
