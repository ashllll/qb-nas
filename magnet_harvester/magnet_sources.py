"""Magnet source extraction policy for crawler and clipboard inputs."""

from __future__ import annotations

from typing import Iterable, List

from magnet_harvester.magnet_parser import extract_from_text


def filter_resolution_items(items: List[dict], allowed: tuple = ("2160p", "4k")) -> List[dict]:
    """Filter crawler results to the configured resolution keywords."""
    allowed_lower = {a.lower() for a in allowed if a}
    if not allowed_lower:
        return items
    return [it for it in items if any(ar in it.get("name", "").lower() for ar in allowed_lower)]


class MagnetSourceExtractor:
    """Extracts Magnet item candidates while applying source-specific policy."""

    def __init__(self, allowed_resolutions: tuple = ("2160p", "4k")) -> None:
        self._allowed_resolutions = allowed_resolutions

    def from_page_result(self, result, *, source_url: str) -> list[dict]:
        items: list[dict] = []
        for text in self._content_sources((result.markdown, result.cleaned_html, result.html)):
            items.extend(extract_from_text(text))

        items = filter_resolution_items(items, allowed=self._allowed_resolutions)
        for item in items:
            item.setdefault("source_url", source_url)
        return items

    def from_clipboard_text(self, text: str) -> list[dict]:
        return extract_from_text(text)

    @staticmethod
    def _content_sources(contents: Iterable[object | None]) -> list[str]:
        sources: list[str] = []
        seen: set[int] = set()
        for content in contents:
            if content:
                if id(content) not in seen:
                    seen.add(id(content))
                    sources.append(str(content))
        return sources
