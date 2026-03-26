from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, field_validator


class TaskStatus(str, Enum):
    pending     = "pending"
    crawling    = "crawling"
    classifying = "classifying"
    adding      = "adding"
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
