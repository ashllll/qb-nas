"""统一错误处理模块"""

from __future__ import annotations

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

    def _generate_error_id(self, category: ErrorCategory, message: str) -> str:
        import hashlib

        key = f"{category.value}:{message}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def record(
        self,
        category: ErrorCategory,
        severity: ErrorSeverity,
        message: str,
        details: dict[str, object] | None = None,
        exc: Optional[Exception] = None,
    ) -> str:
        error_id = self._generate_error_id(category, message)

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
        sorted_errors = sorted(self._errors.items(), key=lambda x: x[1].timestamp)
        to_remove = max(0, len(self._errors) - self._max_errors)
        for error_id, _ in sorted_errors[:to_remove]:
            del self._errors[error_id]

    def get_recent_errors(
        self,
        category: Optional[ErrorCategory] = None,
        severity: Optional[ErrorSeverity] = None,
        limit: int = 50,
    ) -> List[ErrorRecord]:
        with self._lock:
            errors = list(self._errors.values())
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

    def clear_resolved(self):
        with self._lock:
            resolved = [eid for eid, r in self._errors.items() if r.resolved]
            for eid in resolved:
                del self._errors[eid]
        log.info(f"已清理 {len(resolved)} 个已解决的错误记录")


# Module-level singleton
error_handler = ErrorHandler()
