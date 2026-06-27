"""
Logging configuration for Magnet Harvester.

Provides a single configure_logging() entry point called from main.py lifespan.
Console output is INFO+ with compact format; optional file output is DEBUG+ with
full detail and rotation.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


# ── Console formatter ──────────────────────────

class _ColorlessConsoleFormatter(logging.Formatter):
    """Clean console formatter — timestamps, level, message (no ANSI)."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )


# ── File formatter ─────────────────────────────

class _DetailFormatter(logging.Formatter):
    """Full-detail formatter for log files — includes module, line number."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


# ── Public API ─────────────────────────────────

def configure_logging(
    level: str = "INFO",
    log_file: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,   # 10 MB
    backup_count: int = 3,
    quiet_third_party: bool = True,
) -> None:
    """Configure application-wide logging.

    Parameters
    ----------
    level : str
        Root logger level (e.g. "DEBUG", "INFO", "WARNING"). Default "INFO".
    log_file : str | None
        Path to an optional rotating file log. When None, no file handler is
        added.
    max_bytes : int
        Maximum size per log file before rotation (default 10 MB).
    backup_count : int
        Number of rotated backup files to keep (default 3).
    quiet_third_party : bool
        When True, raises httpx, httpcore, and other noisy loggers to
        WARNING level.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any pre-existing handlers (e.g. if configured twice in tests)
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)

    # ── Console handler ──
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_ColorlessConsoleFormatter())
    root.addHandler(console)

    # ── File handler (optional, rotated) ──
    if log_file:
        path = Path(log_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                str(path),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(_DetailFormatter())
            root.addHandler(file_handler)
        except OSError:
            root.warning("无法创建或写入文件日志 %s，跳过文件日志", path)

    # ── Quiet third-party loggers ──
    if quiet_third_party:
        _quiet_logger("httpx", logging.WARNING)
        _quiet_logger("httpcore", logging.WARNING)
        _quiet_logger("urllib3", logging.WARNING)
        _quiet_logger("charset_normalizer", logging.WARNING)
        _quiet_logger("crawl4ai", logging.WARNING)
        _quiet_logger("playwright", logging.WARNING)


def _quiet_logger(name: str, level: int = logging.WARNING) -> None:
    logging.getLogger(name).setLevel(level)
