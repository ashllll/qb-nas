"""
测试配置派生对象
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.config import CrawlerConfig, QBitConfig, Settings


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


def test_persist_qbit_config_replaces_duplicate_keys_used_on_reload(monkeypatch, tmp_path):
    for key in ("QBIT_HOST", "QBIT_USERNAME", "QBIT_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    env_path = tmp_path / ".env"
    env_path.write_text(
        "QBIT_HOST=http://old-first.example:8080\n"
        "QBIT_USERNAME=old-first-user\n"
        "QBIT_PASSWORD=old-first-password\n"
        "QBIT_HOST=http://old-last.example:8080\n"
        "QBIT_USERNAME=old-last-user\n"
        "QBIT_PASSWORD=old-last-password\n",
        encoding="utf-8",
    )
    cfg = Settings(_env_file=None)

    cfg.persist_qbit_config(
        QBitConfig(
            host="http://new.example:8080",
            username="new-user",
            password="new-password",
        ),
        env_path=env_path,
    )

    reloaded = Settings(_env_file=env_path)
    assert reloaded.QBIT_HOST == "http://new.example:8080"
    assert reloaded.QBIT_USERNAME == "new-user"
    assert reloaded.QBIT_PASSWORD == "new-password"


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


def test_qbit_sync_interval_must_be_positive(monkeypatch):
    """QBIT_SYNC_INTERVAL 必须为正：0 会让同步循环变成无休眠紧循环。

    QBitSyncLoop._run 每轮先 wait_for(stop_event, timeout=next_delay())，无失败时
    next_delay() 即该间隔。设为 0 时每轮立即超时并立刻轮询 qB + SQLite。
    """
    from pydantic import ValidationError

    for bad in ("0", "-1", "0.0"):
        monkeypatch.setenv("QBIT_SYNC_INTERVAL", bad)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    # 闭环：合法的下限值必须仍被接受，避免把校验写死成"拒绝一切小值"
    monkeypatch.setenv("QBIT_SYNC_INTERVAL", "0.1")
    assert Settings(_env_file=None).QBIT_SYNC_INTERVAL == 0.1


def test_env_value_round_trips_dollar_signs(tmp_path):
    """含 $ 的值写入 .env 后必须原样读回。

    回归背景：_format_env_value 曾把 $ 转义为 \\$，但 python-dotenv 不做 \\$
    反转义，于是 'p$ss' 落盘为 "p\\$ss"、读回 'p\\\\$ss' —— 通过 UI 保存含 $ 的
    qB 密码后，重启即登录失败，且要到重启后才暴露。
    """
    env_path = tmp_path / ".env"
    for value in ("plainpass", "p$ss", "P@ss$word", "a$b$c", "trailing$", "中文$密码"):
        Settings._write_env_values(env_path, {"QBIT_PASSWORD": value})
        assert Settings(_env_file=env_path).QBIT_PASSWORD == value, (
            f"{value!r} 未能原样读回，落盘内容: {env_path.read_text(encoding='utf-8').strip()}"
        )


def test_env_value_dollar_placeholder_is_rejected_on_persist(tmp_path):
    """经 persist_qbit_config 落盘时，含 ${...} 的值必须被拒绝而不是静默写错。

    回归背景：'p${X}word' 会被 dotenv 当变量插值，写盘后读回 'pword' —— 本次会话
    内存里的配置仍正常，要重启才暴露，属**静默改坏密码**。该形式在 .env 中无法
    表示（python-dotenv 1.2.2 对双引号/单引号/裸值一律插值，且
    DotEnvSettingsSource 不支持关闭插值），因此选择在写入前拒绝。
    """
    from magnet_harvester.config import QBitConfig

    env_path = tmp_path / ".env"
    with pytest.raises(ValueError, match="无法写入"):
        Settings().persist_qbit_config(
            QBitConfig(host="http://h:8080", username="u", password="p$ss${X}word"),
            env_path,
        )
    assert not env_path.exists(), "被拒绝的值不应落盘"

    # 单个 $（非 ${ 形式）不受影响，仍可正常往返
    Settings().persist_qbit_config(
        QBitConfig(host="http://h:8080", username="u", password="p$ss#中文"), env_path
    )
    assert Settings(_env_file=env_path).QBIT_PASSWORD == "p$ss#中文"


def test_security_posture_rejects_real_bind_address_not_just_env():
    """鉴权强制必须依据真实监听地址，而非 SERVICE_HOST 环境变量。

    回归背景：validate_security_posture 只读 SERVICE_HOST（.env.example 默认
    127.0.0.1 → 判定 loopback 后早返回），而文档给出的备用启动方式
    `uvicorn magnet_harvester.main:app --host 0.0.0.0` 会让服务真正绑定到 LAN，
    此时 API_KEY 为空 → require_api_key 直接放行 → LAN 上出现无鉴权写接口。
    """
    # 环境变量仍是 loopback，但真实绑定地址是非 loopback → 必须拒绝
    cfg = Settings(SERVICE_HOST="127.0.0.1", API_KEY="", ALLOW_INSECURE_WRITE_API=False)
    with pytest.raises(RuntimeError, match="non-loopback"):
        cfg.validate_security_posture(bound_host="0.0.0.0")
    with pytest.raises(RuntimeError, match="non-loopback"):
        cfg.validate_security_posture(bound_host="192.168.1.194")

    # 真实绑定地址是 loopback → 放行
    cfg.validate_security_posture(bound_host="127.0.0.1")
    cfg.validate_security_posture(bound_host="::1")
    cfg.validate_security_posture(bound_host="localhost")

    # 显式配置了 API_KEY → 放行（即使绑定到 LAN）
    secured = Settings(SERVICE_HOST="127.0.0.1", API_KEY="k", ALLOW_INSECURE_WRITE_API=False)
    secured.validate_security_posture(bound_host="0.0.0.0")

    # 显式开发豁免 → 放行
    dev = Settings(SERVICE_HOST="127.0.0.1", API_KEY="", ALLOW_INSECURE_WRITE_API=True)
    dev.validate_security_posture(bound_host="0.0.0.0")

    # 真实绑定地址未知时，退回环境变量语义（保持既有行为）
    cfg.validate_security_posture(bound_host=None)


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
