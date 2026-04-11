"""统一错误处理模块 v2.0"""
from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from functools import wraps

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
    details: Dict[str, Any]
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
    _instance: Optional["ErrorHandler"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._errors: Dict[str, ErrorRecord] = {}
        self._error_queue: asyncio.Queue = asyncio.Queue()
        self._max_errors = 1000
        self._auto_recover_enabled = True
    
    def _generate_error_id(self, category: ErrorCategory, message: str) -> str:
        import hashlib
        key = f"{category.value}:{message}"
        return hashlib.md5(key.encode()).hexdigest()[:12]
    
    def record(
        self,
        category: ErrorCategory,
        severity: ErrorSeverity,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        exc: Optional[Exception] = None,
    ) -> str:
        error_id = self._generate_error_id(category, message)
        
        if error_id in self._errors:
            record = self._errors[error_id]
            record.count += 1
            record.details.update(details or {})
            record.timestamp = datetime.now()
            if exc:
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
        
        self._emit_to_queue(record)
        
        if severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL):
            self._handle_critical_error(record)
        
        return error_id
    
    def _emit_to_queue(self, record: ErrorRecord):
        try:
            self._error_queue.put_nowait(record)
        except asyncio.QueueFull:
            pass
    
    def _cleanup_old_errors(self):
        sorted_errors = sorted(
            self._errors.items(),
            key=lambda x: x[1].timestamp
        )
        to_remove = len(self._errors) - self._max_errors + 100
        for error_id, _ in sorted_errors[:to_remove]:
            del self._errors[error_id]
    
    def _handle_critical_error(self, record: ErrorRecord):
        log.critical(
            f"Critical error [{record.error_id}]: {record.message}",
            extra={"details": record.details}
        )
        
        if record.category == ErrorCategory.QBIT:
            pass
    
    def get_recent_errors(
        self,
        category: Optional[ErrorCategory] = None,
        severity: Optional[ErrorSeverity] = None,
        limit: int = 50,
    ) -> List[ErrorRecord]:
        errors = list(self._errors.values())
        
        if category:
            errors = [e for e in errors if e.category == category]
        if severity:
            errors = [e for e in errors if e.severity == severity]
        
        errors.sort(key=lambda x: x.timestamp, reverse=True)
        return errors[:limit]
    
    def get_error_stats(self) -> dict:
        by_category = {}
        by_severity = {}
        
        for record in self._errors.values():
            cat = record.category.value
            sev = record.severity.value
            
            by_category[cat] = by_category.get(cat, 0) + record.count
            by_severity[sev] = by_severity.get(sev, 0) + record.count
        
        return {
            "total_errors": sum(r.count for r in self._errors.values()),
            "unique_errors": len(self._errors),
            "by_category": by_category,
            "by_severity": by_severity,
        }
    
    def clear_resolved(self):
        resolved = [eid for eid, r in self._errors.items() if r.resolved]
        for eid in resolved:
            del self._errors[eid]
        log.info(f"已清理 {len(resolved)} 个已解决的错误记录")
    
    async def wait_for_error(self, timeout: Optional[float] = None) -> Optional[ErrorRecord]:
        try:
            return await asyncio.wait_for(self._error_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None


def handle_errors(
    category: ErrorCategory,
    severity: ErrorSeverity = ErrorSeverity.ERROR,
    reraise: bool = True,
    default_return: Any = None,
):
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            handler = ErrorHandler()
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                handler.record(category, severity, str(e), {"function": func.__name__}, e)
                if reraise:
                    raise
                return default_return
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            handler = ErrorHandler()
            try:
                return func(*args, **kwargs)
            except Exception as e:
                handler.record(category, severity, str(e), {"function": func.__name__}, e)
                if reraise:
                    raise
                return default_return
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper


class GracefulDegradation:
    def __init__(self, fallback_value: Any = None):
        self.fallback_value = fallback_value
        self._failures = 0
        self._failure_threshold = 3
        self._cooldown_until: Optional[datetime] = None
        self._cooldown_seconds = 60
    
    @property
    def is_degraded(self) -> bool:
        if self._cooldown_until and datetime.now() < self._cooldown_until:
            return True
        return self._failures >= self._failure_threshold
    
    def record_success(self):
        self._failures = 0
        self._cooldown_until = None
    
    def record_failure(self):
        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._cooldown_until = datetime.now()
            log.warning(f"进入优雅降级模式，将在 {self._cooldown_seconds} 秒后恢复")
    
    async def execute(self, func, *args, **kwargs):
        if self.is_degraded:
            log.debug("服务处于降级模式，跳过执行")
            return self.fallback_value
        
        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise


error_handler = ErrorHandler()
