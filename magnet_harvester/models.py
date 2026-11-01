from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magnet_harvester.utils.url_validator import URLValidationError, validate_crawl_url


class TaskStatus(str, Enum):
    pending = "pending"
    classifying = "classifying"
    adding = "adding"
    queued = "queued"
    downloading = "downloading"
    # 已加入 qB、处于停止/暂停（qB v5 的 stoppedDL、v4 的 pausedDL）。
    # 语义上属成功路径：种子在 qB 里，只是没在跑，不是失败。
    paused = "paused"
    success = "success"
    error = "error"
    skipped = "skipped"


class MagnetItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    hash: str
    name: str
    magnet: str
    size: Optional[str] = None
    # 来源可追溯：source_url = 用户复制/提交的入口地址（根 URL、magnet://… 或手动输入），
    # detail_url = 真正出现该磁力的详情页（抓取时才有）。
    source_url: str = ""
    detail_url: Optional[str] = None
    # 批次：一次上报/一次抓取任务的标识，UI 用它显示「从 1 个网址收到 8 条」。
    batch_id: Optional[str] = None
    # 来源类型：mac_magnet / mac_url / ui_magnet / ui_url / crawl（历史条目为空）
    source_kind: str = ""
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
        """确保进度值在 0.0–100.0 范围内（含 NaN 防护）。"""
        v = float(v)
        if v != v:  # NaN check
            return 0.0
        return max(0.0, min(v, 100.0))


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
    hashes: List[str] = Field(min_length=1, max_length=500)


class QBitConfigUpdate(BaseModel):
    """qBittorrent 配置更新请求体，替换 routes.py 中的裸 dict。

    所有字段可选：仅更新显式传入的字段。
    """

    qbit_host: Optional[str] = None
    qbit_username: Optional[str] = None
    qbit_password: Optional[str] = None


# ── Mac → NAS 入库（采集中心入口） ──────────────────────


class IngestKind(str, Enum):
    """上报类型：磁力链接或网页地址。"""

    magnet = "magnet"
    url = "url"


class IngestRequest(BaseModel):
    """Mac 代理上报的一条事件。

    幂等键是 ``event_id``（客户端生成，重发返回 duplicate）；磁力另按归一化
    infohash 去重，所以同一条磁力用不同 event_id 重发也不会重复入库。
    """

    event_id: Optional[str] = Field(default=None, max_length=128)
    kind: IngestKind
    value: str = Field(min_length=1, max_length=8192)
    captured_at: Optional[datetime] = None
    device: Optional[str] = Field(default=None, max_length=64)

    @field_validator("value")
    @classmethod
    def strip_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("value 不能为空")
        return v

    @field_validator("event_id")
    @classmethod
    def strip_event_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


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
