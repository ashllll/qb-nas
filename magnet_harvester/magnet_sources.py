"""Magnet source extraction policy for crawler and clipboard inputs."""

from __future__ import annotations

from typing import Iterable, List

from magnet_harvester.magnet_parser import extract_from_text, normalize_infohash


def filter_resolution_items(items: List[dict], allowed: tuple = ()) -> List[dict]:
    """按配置的分辨率关键字过滤；``allowed`` 为空表示不过滤（多条全收）。"""
    allowed_lower = {a.lower() for a in allowed if a}
    if not allowed_lower:
        return items
    return [it for it in items if any(ar in it.get("name", "").lower() for ar in allowed_lower)]


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
        result.append(item)
    return result


class MagnetSourceExtractor:
    """Extracts Magnet item candidates while applying source-specific policy."""

    def __init__(self, allowed_resolutions: tuple = ()) -> None:
        self._allowed_resolutions = allowed_resolutions

    def from_page_result(
        self, result, *, source_url: str, detail_url: str | None = None
    ) -> list[dict]:
        return self.from_html(
            self._result_html(result),
            source_url=source_url,
            detail_url=detail_url or source_url,
        )

    def from_html(self, html: str, *, source_url: str, detail_url: str | None = None) -> list[dict]:
        items: list[dict] = []
        for text in self._content_sources((html,)):
            items.extend(extract_from_text(text))
        items = filter_resolution_items(items, allowed=self._allowed_resolutions)
        return _normalise_items(
            items,
            source_url=source_url,
            detail_url=detail_url or source_url,
        )

    def from_clipboard_text(self, text: str) -> list[dict]:
        return extract_from_text(text)

    @staticmethod
    def _result_html(result) -> str:
        for attr in ("html", "cleaned_html", "markdown"):
            value = getattr(result, attr, "") or ""
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _content_sources(contents: Iterable[object | None]) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()
        for content in contents:
            if content:
                text = str(content)
                if text not in seen:
                    seen.add(text)
                    sources.append(text)
        return sources
