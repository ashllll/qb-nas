"""
测试详情页链接发现策略
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler


def test_extract_detail_links_filters_and_limits():
    crawler = MagnetCrawler(config=CrawlerConfig())
    visited = {"https://example.com/details/old"}
    links = {
        "internal": [
            {"href": "https://example.com/details/123"},
            {"href": "https://example.com/list?page=2"},
            {"href": "https://example.com/view/abc"},
            {"href": "https://other.com/details/999"},
            {"href": "https://example.com/details/old"},
            {"href": "https://example.com/item?tid=42"},
        ]
    }

    result = crawler._extract_detail_links("https://example.com/list", links, visited)

    assert "https://example.com/details/123" in result
    assert "https://example.com/view/abc" in result
    assert "https://example.com/item?tid=42" in result
    assert "https://example.com/list?page=2" not in result
    assert "https://other.com/details/999" not in result
    assert "https://example.com/details/old" not in result


if __name__ == "__main__":
    test_extract_detail_links_filters_and_limits()
    print("=== crawler detail link tests passed! ===")
