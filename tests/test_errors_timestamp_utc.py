"""错误记录时间戳必须带时区（时间契约：进 API 的时间一律 UTC +00:00）。

回归：errors.py 曾用 datetime.now()（naive 本地时间），/api/errors 会输出
"2026-11-01T09:10:33" 这种裸串，前端按 UTC 解析会在非 UTC 浏览器上偏 8 小时。
"""
from __future__ import annotations

from magnet_harvester.errors import ErrorCategory, ErrorHandler, ErrorRecord, ErrorSeverity

TZ_SUFFIXES = ("+00:00", "Z")


def test_error_record_dict_timestamp_has_timezone():
    rec = ErrorRecord(
        error_id="e1",
        category=ErrorCategory.UNKNOWN,
        severity=ErrorSeverity.ERROR,
        message="boom",
        details={},
    )
    ts = rec.to_dict()["timestamp"]
    assert ts.endswith(TZ_SUFFIXES), f"错误时间戳缺时区: {ts}"


def test_error_handler_record_timestamp_has_timezone():
    handler = ErrorHandler()
    handler.record(ErrorCategory.NETWORK, ErrorSeverity.WARNING, "net down")
    row = handler.get_recent_errors(limit=1)[0]
    ts = row.to_dict()["timestamp"]
    assert ts.endswith(TZ_SUFFIXES), f"handler 记录的 error 时间戳缺时区: {ts}"
