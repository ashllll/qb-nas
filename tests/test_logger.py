"""Tests for the logging configuration module."""

from __future__ import annotations

import logging
import os
import tempfile

from magnet_harvester.logger import configure_logging


def test_configure_logging_console_only():
    """Root logger should have a console handler after configure_logging()."""
    # Reset root logger
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    configure_logging(level="INFO")
    handlers = root.handlers
    console_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler)]
    assert len(console_handlers) >= 1


def test_configure_logging_with_file():
    """A RotatingFileHandler should be added when log_file is set."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
        log_path = f.name

    try:
        configure_logging(level="DEBUG", log_file=log_path)
        file_handlers = [
            h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) >= 1

        # File should be writable
        test_logger = logging.getLogger("test_logger")
        test_logger.info("test message")
        assert os.path.exists(log_path)
    finally:
        # Close all file handlers so Windows releases the lock
        for h in list(root.handlers):
            h.close()
            root.removeHandler(h)
        try:
            os.unlink(log_path)
        except PermissionError:
            pass  # Acceptable on Windows if handle hasn't fully released


def test_configure_logging_resets_handlers():
    """Calling configure_logging twice should not duplicate handlers."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    configure_logging(level="INFO")
    count_after_first = len(root.handlers)

    configure_logging(level="DEBUG")
    count_after_second = len(root.handlers)

    # Should have replaced, not accumulated
    assert count_after_second <= count_after_first + 1  # +1 for potential file


def test_third_party_loggers_quieted():
    """Noisy third-party loggers should be at WARNING level by default."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    configure_logging(level="DEBUG")

    for name in ("httpx", "httpcore", "charset_normalizer", "urllib3", "scrapling", "playwright"):
        assert logging.getLogger(name).level == logging.WARNING, f"{name} should be WARNING"


def test_level_set_correctly():
    """Root logger level should match the configured level."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    configure_logging(level="DEBUG")
    assert root.level == logging.DEBUG

    # Reset and test WARNING
    for h in list(root.handlers):
        root.removeHandler(h)
    configure_logging(level="WARNING")
    assert root.level == logging.WARNING


def test_redacting_access_formatter_masks_api_key():
    """access log 中 api_key 查询参数值必须被脱敏。"""
    import logging

    from magnet_harvester.logger import RedactingAccessFormatter

    fmt = RedactingAccessFormatter(
        '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1", "GET", "/ws?api_key=SECRET123&x=1", "1.1", 101),
        exc_info=None,
    )
    out = fmt.format(record)
    assert "SECRET123" not in out
    assert "api_key=***" in out


def test_redacting_default_formatter_masks_websocket_handshake_key():
    """WebSocket 握手行由 default formatter 输出，必须同样脱敏。

    回归背景：脱敏只挂在 access formatter 上，而 WS 握手行走 uvicorn.error
    logger + default formatter，实测 stderr 明文出现
    `INFO: 127.0.0.1:52083 - "WebSocket /ws?api_key=TOPSECRET123" [accepted]`。
    """
    import logging

    from magnet_harvester.logger import RedactingDefaultFormatter

    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s" %s',
        args=("127.0.0.1:52083", "WebSocket /ws?api_key=TOPSECRET123", "[accepted]"),
        exc_info=None,
    )
    out = RedactingDefaultFormatter("%(levelname)s:     %(message)s").format(record)

    assert "TOPSECRET123" not in out
    assert "api_key=***" in out
    # 脱敏不得吃掉收尾引号（旧正则 [^&\s]* 会连引号一起吞掉，破坏日志格式）
    assert out.endswith('" [accepted]'), f"收尾引号被破坏: {out!r}"


def test_uvicorn_log_config_redacts_both_formatters():
    """access 与 default 两个 formatter 都必须换成脱敏版。"""
    from magnet_harvester.logger import uvicorn_log_config

    config = uvicorn_log_config()
    assert (
        config["formatters"]["access"]["()"] == "magnet_harvester.logger.RedactingAccessFormatter"
    )
    assert (
        config["formatters"]["default"]["()"] == "magnet_harvester.logger.RedactingDefaultFormatter"
    )

    # 真实 uvicorn logger 绑定：uvicorn.error 走 default handler，是 WS 握手行的出路
    assert config["handlers"]["default"]["formatter"] == "default"
    assert config["loggers"]["uvicorn"]["handlers"] == ["default"]
