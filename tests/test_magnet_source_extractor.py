from __future__ import annotations

from magnet_harvester.magnet_sources import MagnetSourceExtractor


def test_magnet_source_extractor_applies_source_policy():
    text = (
        "magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "&dn=Movie.1080p.WEB-DL "
        "magnet:?xt=urn:btih:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        "&dn=Movie.2160p.WEB-DL"
    )
    extractor = MagnetSourceExtractor(allowed_resolutions=("2160p",))

    page_items = extractor.from_html(text, source_url="https://example.com/details/1")

    assert [item["hash"] for item in page_items] == ["BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"]
    assert page_items[0]["source_url"] == "https://example.com/details/1"
