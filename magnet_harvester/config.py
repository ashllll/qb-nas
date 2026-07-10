from __future__ import annotations

import logging
import os
import ipaddress
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)


# ── 子配置 ──────────────────────────────


@dataclass
class CrawlerConfig:
    timeout: int = 30
    max_depth: int = 2
    concurrency: int = 6
    max_detail_links: int = 200
    headless: bool = True
    allowed_resolutions: tuple[str, ...] = ("2160p", "4k")
    wait_until: str = "load"
    delay_before_return_html: float = 1.0
    word_count_threshold: int = 10
    scan_full_page: bool = True
    scroll_delay: float = 0.2
    max_scroll_steps: int = 8
    process_iframes: bool = True
    flatten_shadow_dom: bool = True
    remove_overlay_elements: bool = True
    remove_consent_popups: bool = True
    # — v0.9.0 新增 —
    max_retries: int = 1
    check_robots_txt: bool = False
    simulate_user: bool = False
    magics: bool = False
    url_score_depth_bias: bool = True  # BFS 时优先短路径详情页


@dataclass
class QBitConfig:
    host: str = "http://192.168.1.100:8080"
    username: str = "admin"
    password: str = "adminadmin"
    fs_base_path: str = ""


@dataclass
class ServiceConfig:
    host: str = "127.0.0.1"
    port: int = 8899


# ── 主配置 ──────────────────────────────


class Settings(BaseSettings):
    QBIT_HOST: str = "http://192.168.1.100:8080"
    QBIT_USERNAME: str = "admin"
    QBIT_PASSWORD: str = "adminadmin"
    QBIT_SYNC_INTERVAL: float = 2.0

    SERVICE_HOST: str = "127.0.0.1"
    SERVICE_PORT: int = 8899

    CRAWLER_TIMEOUT: int = 30
    CRAWLER_MAX_DEPTH: int = 2
    CRAWLER_CONCURRENCY: int = 6
    CRAWLER_MAX_DETAIL_LINKS: int = 200
    CRAWLER_HEADLESS: bool = True
    CRAWLER_ALLOWED_RESOLUTIONS: str = "2160p,4k"
    CRAWLER_WAIT_UNTIL: str = "load"
    CRAWLER_DELAY_BEFORE_HTML: float = 1.0
    CRAWLER_WORD_COUNT_THRESHOLD: int = 10
    CRAWLER_SCAN_FULL_PAGE: bool = True
    CRAWLER_SCROLL_DELAY: float = 0.2
    CRAWLER_MAX_SCROLL_STEPS: int = 8
    CRAWLER_PROCESS_IFRAMES: bool = True
    CRAWLER_FLATTEN_SHADOW_DOM: bool = True
    CRAWLER_REMOVE_OVERLAYS: bool = True
    CRAWLER_REMOVE_CONSENT_POPUPS: bool = True
    CRAWLER_MAX_RETRIES: int = 1
    CRAWLER_CHECK_ROBOTS_TXT: bool = False
    CRAWLER_SIMULATE_USER: bool = False
    CRAWLER_MAGICS: bool = False
    CRAWLER_URL_SCORE_DEPTH_BIAS: bool = True

    FS_BASE_PATH: str = ""  # 脚本可创建目录的真实路径（如 Z:\downloads），为空则跳过 mkdir

    MIN_DISK_SPACE_GB: float = 10.0
    AUTO_CREATE_DIRS: bool = True

    # ── 日志 ──
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = ""  # 留空仅控制台输出；填写路径则同时输出到滚动文件

    # ── 存储 ──
    STORE_BACKEND: str = "memory"  # "memory" 或 "sqlite"
    STORE_DB_PATH: str = "data/magnet_items.db"

    API_KEY: str = ""  # 为空则禁用 API Key 认证（向后兼容）
    ALLOW_INSECURE_WRITE_API: bool = False
    CORS_ALLOWED_ORIGINS: str = ""  # 为空则禁用 CORS（只允许同域），逗号分隔多个域名

    SITE_COOKIES: str = "{}"  # JSON: {"domain": "cookie1=val1; cookie2=val2"}

    model_config = {
        "env_file": str(Path(__file__).resolve().parent.parent / ".env"),
        "env_file_encoding": "utf-8",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._qbit_config: Optional[QBitConfig] = None
        self._crawler_config: Optional[CrawlerConfig] = None
        self._service_config: Optional[ServiceConfig] = None

    @property
    def qbit(self) -> QBitConfig:
        if self._qbit_config is None:
            self._qbit_config = QBitConfig(
                host=self.QBIT_HOST,
                username=self.QBIT_USERNAME,
                password=self.QBIT_PASSWORD,
                fs_base_path=self.FS_BASE_PATH,
            )
        return self._qbit_config

    @property
    def crawler(self) -> CrawlerConfig:
        if self._crawler_config is None:
            self._crawler_config = CrawlerConfig(
                timeout=self.CRAWLER_TIMEOUT,
                max_depth=self.CRAWLER_MAX_DEPTH,
                concurrency=self.CRAWLER_CONCURRENCY,
                max_detail_links=self.CRAWLER_MAX_DETAIL_LINKS,
                headless=self.CRAWLER_HEADLESS,
                allowed_resolutions=self._parse_csv_tuple(self.CRAWLER_ALLOWED_RESOLUTIONS),
                wait_until=self.CRAWLER_WAIT_UNTIL,
                delay_before_return_html=self.CRAWLER_DELAY_BEFORE_HTML,
                word_count_threshold=self.CRAWLER_WORD_COUNT_THRESHOLD,
                scan_full_page=self.CRAWLER_SCAN_FULL_PAGE,
                scroll_delay=self.CRAWLER_SCROLL_DELAY,
                max_scroll_steps=self.CRAWLER_MAX_SCROLL_STEPS,
                process_iframes=self.CRAWLER_PROCESS_IFRAMES,
                flatten_shadow_dom=self.CRAWLER_FLATTEN_SHADOW_DOM,
                remove_overlay_elements=self.CRAWLER_REMOVE_OVERLAYS,
                remove_consent_popups=self.CRAWLER_REMOVE_CONSENT_POPUPS,
                max_retries=self.CRAWLER_MAX_RETRIES,
                check_robots_txt=self.CRAWLER_CHECK_ROBOTS_TXT,
                simulate_user=self.CRAWLER_SIMULATE_USER,
                magics=self.CRAWLER_MAGICS,
                url_score_depth_bias=self.CRAWLER_URL_SCORE_DEPTH_BIAS,
            )
        return self._crawler_config

    @property
    def service(self) -> ServiceConfig:
        if self._service_config is None:
            self._service_config = ServiceConfig(
                host=self.SERVICE_HOST,
                port=self.SERVICE_PORT,
            )
        return self._service_config

    def build_qbit_config(
        self,
        host: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> QBitConfig:
        """Build a fully validated candidate without mutating live settings."""
        candidate_host = self.QBIT_HOST if host is None else host.strip()
        candidate_username = self.QBIT_USERNAME if username is None else username.strip()
        candidate_password = self.QBIT_PASSWORD if password is None else password.strip()

        if not candidate_host:
            raise ValueError("qBittorrent 主机地址不能为空")
        if not candidate_host.startswith(("http://", "https://")):
            raise ValueError(
                f"非法的 qBittorrent 主机地址: {candidate_host}（必须以 http:// 或 https:// 开头）"
            )
        if not candidate_username:
            raise ValueError("用户名不能为空")
        if not candidate_password:
            raise ValueError("密码不能为空")

        return QBitConfig(
            host=candidate_host,
            username=candidate_username,
            password=candidate_password,
            fs_base_path=self.FS_BASE_PATH,
        )

    def commit_qbit_config(self, config: QBitConfig) -> None:
        self.QBIT_HOST = config.host
        self.QBIT_USERNAME = config.username
        self.QBIT_PASSWORD = config.password
        self._qbit_config = None

    def persist_qbit_config(self, config: QBitConfig, env_path: str | Path | None = None) -> None:
        """Persist qBittorrent connection settings to the .env file."""
        path = Path(env_path or self.model_config.get("env_file", ".env"))
        updates = {
            "QBIT_HOST": config.host,
            "QBIT_USERNAME": config.username,
            "QBIT_PASSWORD": config.password,
        }
        self._write_env_values(path, updates)

    def validate_security_posture(self) -> None:
        """Reject network-exposed write endpoints without explicit protection."""
        host = self.SERVICE_HOST.strip().lower()
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False

        if loopback or self.API_KEY.strip() or self.ALLOW_INSECURE_WRITE_API:
            return
        raise RuntimeError(
            "Refusing to expose unauthenticated write endpoints on a non-loopback address. "
            "Configure API_KEY or set ALLOW_INSECURE_WRITE_API=true for deliberate development use."
        )

    def check_disk_space(self) -> dict[str, object]:
        """Return disk usage for the configured download filesystem path."""
        path = self.FS_BASE_PATH.strip() or "."
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            log.warning("磁盘空间检查失败: %s", exc)
            return {
                "path": path,
                "error": str(exc),
                "min_free_gb": self.MIN_DISK_SPACE_GB,
                "low_space": False,
            }

        free_gb = self._bytes_to_gb(usage.free)
        return {
            "path": path,
            "total_gb": self._bytes_to_gb(usage.total),
            "used_gb": self._bytes_to_gb(usage.used),
            "free_gb": free_gb,
            "min_free_gb": self.MIN_DISK_SPACE_GB,
            "low_space": free_gb < self.MIN_DISK_SPACE_GB,
        }

    @staticmethod
    def _parse_csv_tuple(value: str) -> tuple[str, ...]:
        values = tuple(item.strip() for item in value.split(",") if item.strip())
        return values or ("2160p", "4k")

    @staticmethod
    def _bytes_to_gb(value: int) -> float:
        return round(value / 1024**3, 2)

    @classmethod
    def _write_env_values(cls, path: Path, updates: dict[str, str]) -> None:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
        remaining = dict(updates)
        rendered: list[str] = []

        for line in lines:
            key = cls._env_line_key(line)
            if key in remaining:
                newline = "\n" if line.endswith("\n") else ""
                rendered.append(f"{key}={cls._format_env_value(remaining.pop(key))}{newline}")
            else:
                rendered.append(line)

        if remaining:
            if rendered and not rendered[-1].endswith("\n"):
                rendered[-1] += "\n"
            for key, value in remaining.items():
                rendered.append(f"{key}={cls._format_env_value(value)}\n")

        if not rendered:
            rendered = [f"{k}={cls._format_env_value(v)}\n" for k, v in updates.items()]

        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text("".join(rendered), encoding="utf-8")
            os.replace(tmp, path)  # 原子替换，避免崩溃时文件损坏
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _env_line_key(line: str) -> str | None:
        # 解析范围：仅处理简单 KEY=VALUE 行。
        # 不覆盖：export 前缀、引号包裹值、行内注释（# in value）、
        # 多行值、变量展开（$VAR / ${VAR}）等高级 dotenv 语法。
        # 这些场景在当前项目中不存在，保持简单即可。
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            return None
        if stripped.startswith("export "):
            return None  # 不覆盖 export 前缀行（与注释一致）
        left, _, _ = stripped.partition("=")
        parts = left.strip().split()
        key = parts[-1] if parts else ""
        if not key or not key.replace("_", "").isalnum():
            return None
        return key

    @staticmethod
    def _format_env_value(value: str) -> str:
        escaped = (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace('"', '\\"')
            .replace("$", "\\$")
        )
        return f'"{escaped}"'


settings = Settings()
