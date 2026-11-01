"""时间工具 — 进库/进 API 的时间戳一律 UTC 且带时区（《约定》第 1 条）。

项目里「时间」只有一个语义：UTC 的绝对时刻。容器 ``TZ`` 只影响日志排版，
不参与数据语义；所以任何写库、进响应的 datetime 都必须带 tzinfo（``+00:00``）。

历史数据是 naive 字符串，但当年容器 TZ=UTC（实测 8899 的 created_at
"2026-11-01T08:13:36.898188" 对应本地 16:24+08:00），因此 naive 值按 UTC 解释。
"""

from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc


def utc_now() -> datetime:
    """当前时刻（带 UTC 时区）。所有落库/进响应的时间戳都从这里取。"""
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    """把 datetime 归一到 UTC；naive 值按 UTC 解释（历史行的约定）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    """UTC ISO-8601 字符串，带 ``+00:00``，可直接进库/进响应。"""
    return to_utc(value).isoformat()


def parse_iso_utc(value: str | None) -> datetime | None:
    """解析库里的 ISO 字符串；naive 按 UTC 解释，解析不了返回 None（不抛）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return to_utc(parsed)
