"""
测试配置派生对象
"""

import io
import stat
import sys
import os

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import ConfigPersistError, CrawlerConfig, QBitConfig, Settings


@pytest.mark.parametrize("backend", ["sqlite", "memory"])
def test_store_backend_accepts_supported_values(backend):
    assert Settings(STORE_BACKEND=backend).STORE_BACKEND == backend


@pytest.mark.parametrize("backend", ["sqlite3", "SQLite", " sqlite", "sqlite ", ""])
def test_store_backend_rejects_unknown_values(backend):
    with pytest.raises(ValidationError) as excinfo:
        Settings(STORE_BACKEND=backend)

    message = str(excinfo.value)
    assert "STORE_BACKEND" in message
    assert "sqlite" in message and "memory" in message


def test_store_backend_from_env_file_rejects_unknown_value(tmp_path):
    """启动时真正读的是 .env，不是构造参数；非法值必须在这里就 abort。"""
    env_file = tmp_path / ".env"
    env_file.write_text("STORE_BACKEND=postgres\n", encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=env_file)

    assert "STORE_BACKEND" in str(excinfo.value)


def test_crawler_allowed_resolutions_parse_csv():
    cfg = Settings(CRAWLER_ALLOWED_RESOLUTIONS="1080p, 2160p, 4k")

    assert cfg.crawler.allowed_resolutions == ("1080p", "2160p", "4k")


def test_crawler_allowed_resolutions_empty_disables_filtering():
    """空值 = 不过滤分辨率：详情页 1080p/720p 也要收（采集中心约定）。"""
    cfg = Settings(CRAWLER_ALLOWED_RESOLUTIONS="")

    assert cfg.crawler.allowed_resolutions == ()
    assert Settings().crawler.allowed_resolutions == ()


def test_default_crawler_per_host_concurrency_is_tuned_for_detail_pages():
    cfg = Settings()

    assert cfg.CRAWLER_PER_HOST_CONCURRENCY == 4
    assert cfg.crawler.per_host_concurrency == 4


def test_default_crawler_uses_scrapling_speed_optimizations():
    cfg = Settings()

    assert cfg.CRAWLER_DISABLE_RESOURCES is True
    assert cfg.CRAWLER_HTTP_FIRST is True
    assert cfg.crawler.http_first is True
    assert cfg.crawler.disable_resources is True


def test_default_crawler_detail_link_limit_keeps_large_result_sets():
    cfg = Settings()

    assert cfg.CRAWLER_MAX_DETAIL_LINKS == 200
    assert cfg.crawler.max_detail_links == 200


def test_crawler_config_normalizes_unsafe_boundaries():
    config = CrawlerConfig(
        timeout=0,
        max_depth=0,
        per_host_concurrency=0,
        max_detail_links=-1,
        delay_before_return_html=-1,
        scroll_delay=-1,
        max_scroll_steps=-1,
        max_retries=-1,
        wait_until="typo",
    )

    assert config.timeout == 1
    assert config.max_depth == 1
    assert config.per_host_concurrency == 1
    assert config.per_host_concurrency == 1
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


@pytest.mark.parametrize(
    "password",
    [
        "pa$ss",
        "pa${HOME}ss",
        "pa$$word",
        "  leading and trailing  ",
        'pa"ss word',
        "back\\slash",
        "tab\there",
    ],
)
def test_qbit_password_round_trips_through_env_file(tmp_path, password):
    """界面保存后重启读回的密码必须逐字符相等：$ / ${HOME} / 首尾空格都不许被改写。"""
    env_path = tmp_path / ".env"
    env_path.write_text("QBIT_HOST=http://old.example:8080\n", encoding="utf-8")
    cfg = Settings(_env_file=str(env_path))

    cfg.persist_qbit_config(
        QBitConfig(host="http://new.example:8080", username="new-user", password=password),
        env_path=env_path,
    )
    reloaded = Settings(_env_file=str(env_path))

    assert reloaded.QBIT_PASSWORD == password
    assert reloaded.build_qbit_config().password == password


def test_env_file_empty_values_are_not_replaced_by_defaults(tmp_path):
    """空值必须保持为空：`QBIT_PASSWORD=` 不能被默认 adminadmin 顶掉。"""
    env_path = tmp_path / ".env"
    env_path.write_text("QBIT_PASSWORD=\nQBit_Username=\n", encoding="utf-8")

    settings = Settings(_env_file=str(env_path))

    assert settings.QBIT_PASSWORD == ""
    assert settings.QBIT_USERNAME == ""


def test_build_qbit_config_keeps_password_verbatim():
    """首尾空格属于密码本身，不能 .strip()。"""
    built = Settings().build_qbit_config(password="  padded  ")

    assert built.password == "  padded  "


def test_format_env_value_is_read_back_verbatim():
    """写出的值只用 dotenv 认识的转义，不靠 `$` 反斜杠这种不存在的语法。"""
    value = 'pa$ss ${HOME} "q" back\\slash 尾 '

    rendered = Settings._format_env_value(value)

    assert "\\$" not in rendered
    with io.StringIO(f"A={rendered}\n") as stream:
        assert dotenv_values(stream=stream, interpolate=False)["A"] == value


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
    test_default_crawler_per_host_concurrency_is_tuned_for_detail_pages()
    test_default_crawler_detail_link_limit_keeps_large_result_sets()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        test_persist_qbit_config_updates_env_without_dropping_other_values(Path(temp_dir))
    print("=== config tests passed! ===")
