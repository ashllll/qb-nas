from __future__ import annotations

from dataclasses import dataclass

from magnet_harvester.magnet_sources import MagnetSourceExtractor


@dataclass
class FakeCrawlResult:
    markdown: str | None = None
    cleaned_html: str | None = None
    html: str | None = None


def test_magnet_source_extractor_applies_source_policy():
    text = (
        "magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "&dn=Movie.1080p.WEB-DL "
        "magnet:?xt=urn:btih:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        "&dn=Movie.2160p.WEB-DL"
    )
    extractor = MagnetSourceExtractor(allowed_resolutions=("2160p",))

    page_items = extractor.from_page_result(
        FakeCrawlResult(markdown=text, cleaned_html=text),
        source_url="https://example.com/details/1",
    )
    clipboard_items = extractor.from_clipboard_text(text)

    assert [item["hash"] for item in page_items] == ["BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"]
    assert page_items[0]["source_url"] == "https://example.com/details/1"
    assert {item["hash"] for item in clipboard_items} == {
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    }
