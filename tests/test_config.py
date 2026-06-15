"""
测试配置派生对象
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import Settings


def test_crawler_allowed_resolutions_parse_csv():
    cfg = Settings(CRAWLER_ALLOWED_RESOLUTIONS="1080p, 2160p, 4k")

    assert cfg.crawler.allowed_resolutions == ("1080p", "2160p", "4k")


def test_crawler_allowed_resolutions_falls_back_when_empty():
    cfg = Settings(CRAWLER_ALLOWED_RESOLUTIONS="")

    assert cfg.crawler.allowed_resolutions == ("2160p", "4k")


def test_default_crawler_concurrency_is_tuned_for_detail_pages():
    cfg = Settings()

    assert cfg.CRAWLER_CONCURRENCY == 6
    assert cfg.crawler.concurrency == 6


def test_default_crawler_detail_link_limit_keeps_large_result_sets():
    cfg = Settings()

    assert cfg.CRAWLER_MAX_DETAIL_LINKS == 200
    assert cfg.crawler.max_detail_links == 200


if __name__ == "__main__":
    test_crawler_allowed_resolutions_parse_csv()
    test_crawler_allowed_resolutions_falls_back_when_empty()
    test_default_crawler_concurrency_is_tuned_for_detail_pages()
    test_default_crawler_detail_link_limit_keeps_large_result_sets()
    print("=== config tests passed! ===")
