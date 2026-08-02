"""统一错误处理模块"""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

log = logging.getLogger(__name__)


class ErrorSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorCategory(str, Enum):
    NETWORK = "network"
    CRAWLER = "crawler"
    CLASSIFIER = "classifier"
    QBIT = "qbit"
    STORAGE = "storage"
    CONFIG = "config"
    UNKNOWN = "unknown"


@dataclass
class ErrorRecord:
    error_id: str
    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    details: dict[str, object]
    timestamp: datetime = field(default_factory=datetime.now)
    traceback: Optional[str] = None
    count: int = 1
    resolved: bool = False

    def to_dict(self) -> dict:
        return {
            "error_id": self.error_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "count": self.count,
            "resolved": self.resolved,
        }


class ErrorHandler:
    def __init__(self):
        self._errors: dict[str, ErrorRecord] = {}
        self._max_errors = 1000
        self._lock = threading.Lock()

    def _generate_error_id(
        self, category: ErrorCategory, message: str, details: dict[str, object] | None = None
    ) -> str:
        key = f"{category.value}:{message}"
        if details:
            detail_parts = sorted(f"{k}={v!r}" for k, v in details.items())
            key += ":" + ":".join(detail_parts)
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def record(
        self,
        category: ErrorCategory,
        severity: ErrorSeverity,
        message: str,
        details: dict[str, object] | None = None,
        exc: Optional[Exception] = None,
    ) -> str:
        error_id = self._generate_error_id(category, message, details)

        with self._lock:
            if error_id in self._errors:
                record = self._errors[error_id]
                record.count += 1
                record.details.update(details or {})
                record.timestamp = datetime.now()
                # BUG-26: 只在第一次出现时记录 traceback，重复时不覆盖
                if exc and record.traceback is None:
                    record.traceback = traceback.format_exc()
            else:
                record = ErrorRecord(
                    error_id=error_id,
                    category=category,
                    severity=severity,
                    message=message,
                    details=details or {},
                    traceback=traceback.format_exc() if exc else None,
                )
                self._errors[error_id] = record

                if len(self._errors) > self._max_errors:
                    self._cleanup_old_errors()

        if severity == ErrorSeverity.ERROR:
            log.error(
                f"Error [{record.error_id}]: {record.message}", extra={"details": record.details}
            )
        elif severity == ErrorSeverity.CRITICAL:
            log.critical(
                f"Critical error [{record.error_id}]: {record.message}",
                extra={"details": record.details},
            )

        return error_id

    def _cleanup_old_errors(self):
        """调用方必须已持有 self._lock"""
        while len(self._errors) > self._max_errors:
            oldest_id = min(self._errors, key=lambda eid: self._errors[eid].timestamp)
            del self._errors[oldest_id]

    def get_recent_errors(
        self,
        category: Optional[ErrorCategory] = None,
        severity: Optional[ErrorSeverity] = None,
        limit: int = 50,
    ) -> List[ErrorRecord]:
        with self._lock:
            errors = [copy.copy(e) for e in self._errors.values()]
        if category:
            errors = [e for e in errors if e.category == category]
        if severity:
            errors = [e for e in errors if e.severity == severity]
        errors.sort(key=lambda x: x.timestamp, reverse=True)
        return errors[:limit]

    def get_error_stats(self) -> dict:
        with self._lock:
            records = list(self._errors.values())
        by_category = {}
        by_severity = {}
        for record in records:
            cat = record.category.value
            sev = record.severity.value
            by_category[cat] = by_category.get(cat, 0) + record.count
            by_severity[sev] = by_severity.get(sev, 0) + record.count
        return {
            "total_errors": sum(r.count for r in records),
            "unique_errors": len(records),
            "by_category": by_category,
            "by_severity": by_severity,
        }

    def clear_all(self):
        with self._lock:
            count = len(self._errors)
            self._errors.clear()
        log.info(f"已清理 {count} 个错误记录")

    def mark_resolved(self, error_id: str) -> bool:
        """标记指定错误为已解决，为未来扩展预留。"""
        with self._lock:
            record = self._errors.get(error_id)
            if record is None:
                return False
            record.resolved = True
            return True


# Module-level singleton
error_handler = ErrorHandler()
