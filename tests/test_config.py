"""
测试配置派生对象
"""

import stat
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import ConfigPersistError, CrawlerConfig, QBitConfig, Settings


def test_crawler_allowed_resolutions_parse_csv():
    cfg = Settings(CRAWLER_ALLOWED_RESOLUTIONS="1080p, 2160p, 4k")

    assert cfg.crawler.allowed_resolutions == ("1080p", "2160p", "4k")


def test_crawler_allowed_resolutions_empty_disables_filtering():
    """空值 = 不过滤分辨率：详情页 1080p/720p 也要收（采集中心约定）。"""
    cfg = Settings(CRAWLER_ALLOWED_RESOLUTIONS="")

    assert cfg.crawler.allowed_resolutions == ()
    assert Settings().crawler.allowed_resolutions == ()


def test_default_crawler_concurrency_is_tuned_for_detail_pages():
    cfg = Settings()

    assert cfg.CRAWLER_CONCURRENCY == 6
    assert cfg.crawler.concurrency == 6


def test_default_crawler_uses_scrapling_speed_optimizations():
    cfg = Settings()

    assert cfg.CRAWLER_DISABLE_RESOURCES is True
    assert cfg.CRAWLER_BLOCK_ADS is True
    assert cfg.CRAWLER_HTTP_FIRST is True
    assert cfg.crawler.http_first is True
    assert cfg.crawler.disable_resources is True
    assert cfg.crawler.block_ads is True


def test_default_crawler_detail_link_limit_keeps_large_result_sets():
    cfg = Settings()

    assert cfg.CRAWLER_MAX_DETAIL_LINKS == 200
    assert cfg.crawler.max_detail_links == 200


def test_crawler_config_normalizes_unsafe_boundaries():
    config = CrawlerConfig(
        timeout=0,
        max_depth=0,
        concurrency=0,
        max_detail_links=-1,
        delay_before_return_html=-1,
        scroll_delay=-1,
        max_scroll_steps=-1,
        max_retries=-1,
        wait_until="typo",
    )

    assert config.timeout == 1
    assert config.max_depth == 1
    assert config.concurrency == 1
    assert config.max_detail_links == 0
    assert config.delay_before_return_html == 0
    assert config.scroll_delay == 0
    assert config.max_scroll_steps == 0
    assert config.max_retries == 0
    assert config.wait_until == "load"


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


def test_check_disk_space_reports_configured_path(monkeypatch, tmp_path):
    import magnet_harvester.config as config_module

    class FakeDiskUsage:
        total = 100 * 1024**3
        used = 80 * 1024**3
        free = 20 * 1024**3

    calls = []

    def fake_disk_usage(path):
        calls.append(path)
        return FakeDiskUsage()

    monkeypatch.setattr(config_module.shutil, "disk_usage", fake_disk_usage)

    cfg = Settings(FS_BASE_PATH=str(tmp_path), MIN_DISK_SPACE_GB=25.0)

    assert cfg.check_disk_space() == {
        "path": str(tmp_path),
        "total_gb": 100.0,
        "used_gb": 80.0,
        "free_gb": 20.0,
        "min_free_gb": 25.0,
        "low_space": True,
    }
    assert calls == [str(tmp_path)]


def test_allow_fake_ip_config_wires_to_crawler():
    """CRAWLER_ALLOW_FAKE_IP=True 应传播到 CrawlerConfig 和 MagnetCrawler。"""
    from magnet_harvester.crawler import MagnetCrawler

    cfg = Settings(CRAWLER_ALLOW_FAKE_IP=True)
    assert cfg.CRAWLER_ALLOW_FAKE_IP is True
    assert cfg.crawler.allow_fake_ip is True

    crawler = MagnetCrawler(config=cfg.crawler)
    assert crawler._target_admission._allow_fake_ip is True

    # 默认关闭（显式传参，避免被 .env / 环境变量干扰）
    default_cfg = Settings(CRAWLER_ALLOW_FAKE_IP=False)
    assert default_cfg.CRAWLER_ALLOW_FAKE_IP is False
    assert default_cfg.crawler.allow_fake_ip is False


def test_qbit_keys_in_config_file_win_over_process_env(tmp_path, monkeypatch):
    """唯一事实源：文件里的 qB 配置压过进程环境。

    容器若用 compose 的 env_file / -e 注入 QBIT_*（旧部署就是这样），pydantic 默认
    「环境 > 文件」会让界面刚写入的值在重启时被旧值盖回去。这条测试锁住反过来的优先级。
    """
    env_path = tmp_path / ".env"
    env_path.write_text(
        'QBIT_HOST=http://from-file:8085\nQBIT_USERNAME=file-user\nQBIT_PASSWORD="file-pass"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("QBIT_HOST", "http://from-process-env:8080")
    monkeypatch.setenv("QBIT_USERNAME", "env-user")
    monkeypatch.setenv("QBIT_PASSWORD", "env-pass")

    cfg = Settings(_env_file=str(env_path))

    assert cfg.QBIT_HOST == "http://from-file:8085"
    assert cfg.QBIT_USERNAME == "file-user"
    assert cfg.QBIT_PASSWORD == "file-pass"


def test_qbit_keys_fall_back_to_process_env_when_file_has_none(tmp_path, monkeypatch):
    """文件里没有这个键时，环境变量仍然有效（迁移期/首次启动）。"""
    env_path = tmp_path / ".env"
    env_path.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")
    monkeypatch.setenv("QBIT_HOST", "http://from-process-env:8080")

    cfg = Settings(_env_file=str(env_path))

    assert cfg.QBIT_HOST == "http://from-process-env:8080"


def test_deployment_keys_stay_pinned_by_process_env(tmp_path, monkeypatch):
    """部署键由 compose 钉死：.env 里写错端口不会让端口映射对不上。"""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SERVICE_HOST=127.0.0.1\nSERVICE_PORT=9999\nSTORE_DB_PATH=/tmp/wrong.db\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVICE_HOST", "0.0.0.0")
    monkeypatch.setenv("SERVICE_PORT", "8899")
    monkeypatch.setenv("STORE_DB_PATH", "/data/magnet_items.db")

    cfg = Settings(_env_file=str(env_path))

    assert cfg.SERVICE_HOST == "0.0.0.0"
    assert cfg.SERVICE_PORT == 8899
    assert cfg.STORE_DB_PATH == "/data/magnet_items.db"


def test_persist_qbit_config_keeps_file_private_and_leaves_no_residue(tmp_path):
    """界面保存后文件仍是 600，临时文件不残留（旧实现 rename 出来是 644）。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env_path = config_dir / ".env"
    env_path.write_text("API_KEY=secret-key\nQBIT_HOST=http://old:8080\n", encoding="utf-8")
    env_path.chmod(0o600)
    cfg = Settings(_env_file=str(env_path))

    cfg.persist_qbit_config(
        QBitConfig(host="http://new:8080", username="u", password="p"),
        env_path=env_path,
    )

    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert list(config_dir.glob("*.tmp")) == []
    text = env_path.read_text(encoding="utf-8")
    assert "API_KEY=secret-key" in text
    assert 'QBIT_HOST="http://new:8080"' in text


@pytest.mark.skipif(os.geteuid() == 0, reason="root 不受目录权限限制")
def test_persist_qbit_config_error_names_path_uid_and_directory_mode(tmp_path):
    """写不进去时，错误里带路径/uid/目录权限——不用再猜 Permission denied 出在哪一层。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env_path = config_dir / ".env"
    env_path.write_text("QBIT_HOST=http://old:8080\n", encoding="utf-8")
    env_path.chmod(0o600)
    cfg = Settings(_env_file=str(env_path))

    config_dir.chmod(0o500)
    try:
        with pytest.raises(ConfigPersistError) as excinfo:
            cfg.persist_qbit_config(
                QBitConfig(host="http://new:8080", username="u", password="p"),
                env_path=env_path,
            )
    finally:
        config_dir.chmod(0o700)

    message = str(excinfo.value)
    assert str(env_path) in message
    assert f"uid={os.getuid()}" in message
    assert "目录" in message


def test_config_source_report_points_at_file_without_leaking_values(tmp_path):
    """启动自检报告：指出读的是哪个文件、每个键来自哪里，但不含任何密钥值。"""
    env_path = tmp_path / ".env"
    env_path.write_text(
        'QBIT_HOST="http://file:8085"\nQBIT_USERNAME=file-user\nQBIT_PASSWORD="sup3r-secret"\n',
        encoding="utf-8",
    )
    cfg = Settings(_env_file=str(env_path))

    report = cfg.describe_config_source()

    assert report["file"] == str(env_path)
    assert report["sources"]["QBIT_PASSWORD"] == "file"
    assert report["sources"]["QBIT_HOST"] == "file"
    assert "sup3r-secret" not in repr(report)


if __name__ == "__main__":
    test_crawler_allowed_resolutions_parse_csv()
    test_crawler_allowed_resolutions_empty_disables_filtering()
    test_default_crawler_concurrency_is_tuned_for_detail_pages()
    test_default_crawler_detail_link_limit_keeps_large_result_sets()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        test_persist_qbit_config_updates_env_without_dropping_other_values(Path(temp_dir))
    print("=== config tests passed! ===")
