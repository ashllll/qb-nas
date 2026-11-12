"""Magnet source extraction policy for crawler and clipboard inputs."""

from __future__ import annotations

from typing import List

from magnet_harvester.magnet_parser import extract_from_text, normalize_infohash


def filter_resolution_items(items: List[dict], allowed: tuple = ()) -> List[dict]:
    """按配置的分辨率关键字过滤；``allowed`` 为空表示不过滤（多条全收）。

    没有 dn 的磁力无法判断分辨率，保留它；否则一条正常的「只有 hash」的磁力
    会因为名字叫 Unknown_xxx 被静默丢掉。
    """
    allowed_lower = {a.lower() for a in allowed if a}
    if not allowed_lower:
        return items
    return [
        it
        for it in items
        if not it.get("has_dn") or any(ar in it.get("name", "").lower() for ar in allowed_lower)
    ]


def _normalise_items(items: List[dict], *, source_url: str, detail_url: str) -> List[dict]:
    """统一 hash 写法、补齐来源字段，并按归一化 hash 去掉同页重复。"""
    result: List[dict] = []
    seen: set[str] = set()
    for item in items:
        normalised = normalize_infohash(item.get("hash", "")) or item.get("hash")
        if not normalised or normalised in seen:
            continue
        seen.add(normalised)
        item["hash"] = normalised
        item["source_url"] = source_url
        item["detail_url"] = detail_url
        item.pop("has_dn", None)  # 内部过滤标记，不进入 MagnetItem
        result.append(item)
    return result


class MagnetSourceExtractor:
    """Extracts Magnet item candidates while applying source-specific policy."""

    def __init__(self, allowed_resolutions: tuple = ()) -> None:
        self._allowed_resolutions = allowed_resolutions

    def from_html(self, html: str, *, source_url: str, detail_url: str | None = None) -> list[dict]:
        items = extract_from_text(html) if html else []
        items = filter_resolution_items(items, allowed=self._allowed_resolutions)
        return _normalise_items(
            items,
            source_url=source_url,
            detail_url=detail_url or source_url,
        )
