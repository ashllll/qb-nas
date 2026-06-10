"""
测试分辨率过滤 — 只保留含 2160p / 4k 的磁力
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.crawler import filter_resolution_items
from magnet_harvester.magnet_parser import parse_magnet


# ── 测试过滤函数 ──

def test_keep_2160p():
    items = [
        parse_magnet("magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&dn=Test+Movie+2160p+BluRay"),
        parse_magnet("magnet:?xt=urn:btih:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB&dn=Test+Movie+1080p+BluRay"),
    ]
    filtered = filter_resolution_items(items)
    assert len(filtered) == 1
    assert "2160p" in filtered[0]["name"]


def test_keep_4k():
    items = [
        parse_magnet("magnet:?xt=urn:btih:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC&dn=Test+Movie+4K+BluRay"),
    ]
    filtered = filter_resolution_items(items)
    assert len(filtered) == 1


def test_drop_1080p():
    items = [
        parse_magnet("magnet:?xt=urn:btih:DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD&dn=Test+Movie+1080p+BluRay"),
        parse_magnet("magnet:?xt=urn:btih:EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE&dn=Test+Movie+720p+WEB"),
    ]
    filtered = filter_resolution_items(items)
    assert len(filtered) == 0


def test_mixed():
    items = [
        parse_magnet("magnet:?xt=urn:btih:FFFFFFFFFFF11111111111111111111111111111&dn=Example.Movie.2160p.WEB-DL"),
        parse_magnet("magnet:?xt=urn:btih:FFFFFFFFFFF22222222222222222222222222222&dn=Example.Movie.1080p.x264"),
        parse_magnet("magnet:?xt=urn:btih:FFFFFFFFFFF33333333333333333333333333333&dn=Example.Movie.480p.XviD"),
        parse_magnet("magnet:?xt=urn:btih:4444444444444444444444444444444444444444&dn=Some.Movie.4K.UHD"),
    ]
    filtered = filter_resolution_items(items)
    assert len(filtered) == 2
    hashes = {f["hash"] for f in filtered}
    assert "FFFFFFFFFFF11111111111111111111111111111" in hashes
    assert "4444444444444444444444444444444444444444" in hashes


def test_empty():
    assert filter_resolution_items([]) == []


if __name__ == "__main__":
    test_keep_2160p()
    test_keep_4k()
    test_drop_1080p()
    test_mixed()
    test_empty()
    print("=== Resolution filter tests passed! ===")
