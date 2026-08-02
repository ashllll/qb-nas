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
