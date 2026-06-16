"""
测试配置派生对象
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import QBitConfig, Settings


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


def test_persist_qbit_config_updates_env_without_dropping_other_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# existing config\n"
        "QBIT_HOST=http://old.example:8080\n"
        "QBIT_USERNAME=old-user\n"
        "OTHER_VALUE=keep-me\n",
        encoding="utf-8",
    )
    cfg = Settings()

    cfg.persist_qbit_config(
        QBitConfig(
            host="http://new.example:8080",
            username="new-user",
            password='pa"ss word',
        ),
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "# existing config" in text
    assert 'QBIT_HOST="http://new.example:8080"' in text
    assert 'QBIT_USERNAME="new-user"' in text
    assert 'QBIT_PASSWORD="pa\\"ss word"' in text
    assert "OTHER_VALUE=keep-me" in text
    assert "old.example" not in text


if __name__ == "__main__":
    test_crawler_allowed_resolutions_parse_csv()
    test_crawler_allowed_resolutions_falls_back_when_empty()
    test_default_crawler_concurrency_is_tuned_for_detail_pages()
    test_default_crawler_detail_link_limit_keeps_large_result_sets()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        test_persist_qbit_config_updates_env_without_dropping_other_values(Path(temp_dir))
    print("=== config tests passed! ===")
