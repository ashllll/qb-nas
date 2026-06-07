"""
测试 ErrorHandler — 验证可独立实例化、无单例
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.errors import (
    ErrorHandler,
    ErrorCategory,
    ErrorSeverity,
)


def test_independent_instances():
    """两个 ErrorHandler 实例互不干扰"""
    h1 = ErrorHandler()
    h2 = ErrorHandler()

    h1.record(ErrorCategory.CRAWLER, ErrorSeverity.ERROR, "error from h1")
    assert len(h1.get_recent_errors()) == 1
    assert len(h2.get_recent_errors()) == 0, "h2 不应受 h1 影响"


def test_no_singleton():
    """ErrorHandler() 每次都创建新实例"""
    h1 = ErrorHandler()
    h2 = ErrorHandler()
    assert h1 is not h2, "应创建不同实例"


def test_error_dedup():
    """相同 category+message 合并为一条记录"""
    h = ErrorHandler()
    h.record(ErrorCategory.CRAWLER, ErrorSeverity.ERROR, "same error")
    h.record(ErrorCategory.CRAWLER, ErrorSeverity.ERROR, "same error")
    assert len(h.get_recent_errors()) == 1
    assert h.get_recent_errors()[0].count == 2


def test_error_stats():
    """get_error_stats 返回正确的统计"""
    h = ErrorHandler()
    h.record(ErrorCategory.CRAWLER, ErrorSeverity.ERROR, "crawl error")
    h.record(ErrorCategory.CLASSIFIER, ErrorSeverity.WARNING, "classify warn")
    stats = h.get_error_stats()
    assert stats["total_errors"] == 2
    assert stats["unique_errors"] == 2


if __name__ == "__main__":
    test_independent_instances()
    test_no_singleton()
    test_error_dedup()
    test_error_stats()
    print("=== ErrorHandler tests passed! ===")
