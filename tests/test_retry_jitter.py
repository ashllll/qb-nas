"""
重试/批量调度交给 crawl4ai 配置处理。
"""

from crawl4ai import CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler


def test_run_config_uses_crawl4ai_semaphore_instead_of_manual_retry_loop():
    crawler = MagnetCrawler(config=CrawlerConfig(concurrency=7))
    strategy = crawler._build_deep_crawl_strategy(depth=2)

    cfg = crawler._build_run_config(stream=True, deep_crawl_strategy=strategy)

    assert cfg.cache_mode == CacheMode.BYPASS
    assert cfg.stream is True
    assert cfg.semaphore_count == 7
    assert cfg.deep_crawl_strategy is strategy


def test_deep_crawl_strategy_uses_crawl4ai_page_limit():
    crawler = MagnetCrawler(config=CrawlerConfig(max_detail_links=33))

    strategy = crawler._build_deep_crawl_strategy(depth=2)

    assert isinstance(strategy, BFSDeepCrawlStrategy)
    assert strategy.max_pages == 34


def test_run_config_uses_configured_word_count_threshold():
    crawler = MagnetCrawler(config=CrawlerConfig(word_count_threshold=12))

    cfg = crawler._build_run_config()

    assert cfg.word_count_threshold == 12


if __name__ == "__main__":
    test_run_config_uses_crawl4ai_semaphore_instead_of_manual_retry_loop()
    test_deep_crawl_strategy_uses_crawl4ai_page_limit()
    print("=== crawl4ai retry/deep crawl config tests passed! ===")
