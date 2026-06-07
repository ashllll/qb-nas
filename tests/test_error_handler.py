"""
测试 ErrorHandler — 验证无单例、可独立实例化
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio

from magnet_harvester.errors import (
    ErrorHandler,
    ErrorCategory,
    ErrorSeverity,
    handle_errors,
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


def test_module_level_instance_is_plain():
    """模块级 error_handler 是一个普通实例，非单例强制"""
    from magnet_harvester.errors import error_handler
    new_instance = ErrorHandler()
    assert error_handler is not new_instance


@handle_errors(ErrorCategory.CLASSIFIER, reraise=False)
def _function_with_handler():
    raise ValueError("test error")


def test_handle_errors_decorator():
    """装饰器正常 catch 异常"""
    result = _function_with_handler()
    assert result is None  # default_return


if __name__ == "__main__":
    test_independent_instances()
    test_no_singleton()
    test_module_level_instance_is_plain()
    test_handle_errors_decorator()
    print("=== ErrorHandler tests passed! ===")
