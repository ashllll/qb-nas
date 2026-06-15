"""
爬虫链接去重/遍历由 crawl4ai 深爬策略负责。
"""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import CrawlAdmissionFilter, MagnetCrawler


def _crawler_source() -> str:
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(repo_root, "magnet_harvester", "crawler.py"), encoding="utf-8") as f:
        return f.read()


def test_manual_visited_claim_wheel_removed():
    source = _crawler_source()

    assert "_claim_unvisited_links" not in source
    assert "_visited_lock" not in source
    assert "BFSDeepCrawlStrategy" in source


def test_crawl_admission_filter_is_in_deep_crawl_filter_chain():
    crawler = MagnetCrawler(config=CrawlerConfig())
    strategy = crawler._build_deep_crawl_strategy(depth=2)

    assert any(isinstance(filter_, CrawlAdmissionFilter) for filter_ in strategy.filter_chain.filters)


def test_no_manual_worker_loop_methods_remain():
    tree = ast.parse(_crawler_source())
    method_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "_crawl_worker" not in method_names
    assert "_crawl_url_batch" not in method_names
    assert "_fetch_many_stream" not in method_names


if __name__ == "__main__":
    test_manual_visited_claim_wheel_removed()
    test_crawl_admission_filter_is_in_deep_crawl_filter_chain()
    test_no_manual_worker_loop_methods_remain()
    print("=== crawler wheel removal tests passed! ===")
