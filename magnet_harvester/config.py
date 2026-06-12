from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)


# ── 子配置 ──────────────────────────────

@dataclass
class CrawlerConfig:
    timeout: int = 30
    max_depth: int = 2
    concurrency: int = 3
    headless: bool = True
    allowed_resolutions: tuple[str, ...] = ("2160p", "4k")


@dataclass
class QBitConfig:
    host: str = "http://192.168.1.100:8080"
    username: str = "admin"
    password: str = "adminadmin"


@dataclass
class ServiceConfig:
    host: str = "0.0.0.0"
    port: int = 8899


# ── 主配置 ──────────────────────────────

class Settings(BaseSettings):
    QBIT_HOST: str = "http://192.168.1.100:8080"
    QBIT_USERNAME: str = "admin"
    QBIT_PASSWORD: str = "adminadmin"

    SERVICE_HOST: str = "0.0.0.0"
    SERVICE_PORT: int = 8899

    CRAWLER_TIMEOUT: int = 30
    CRAWLER_MAX_DEPTH: int = 2
    CRAWLER_CONCURRENCY: int = 3
    CRAWLER_HEADLESS: bool = True
    CRAWLER_ALLOWED_RESOLUTIONS: str = "2160p,4k"

    FS_BASE_PATH: str = ""  # 脚本可创建目录的真实路径（如 Z:\downloads），为空则跳过 mkdir

    MIN_DISK_SPACE_GB: float = 10.0
    AUTO_CREATE_DIRS: bool = True

    API_KEY: str = ""  # 为空则禁用 API Key 认证（向后兼容）
    CORS_ALLOWED_ORIGINS: str = ""  # 为空则禁用 CORS（只允许同域），逗号分隔多个域名

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

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
            )
        return self._qbit_config

    @property
    def crawler(self) -> CrawlerConfig:
        if self._crawler_config is None:
            self._crawler_config = CrawlerConfig(
                timeout=self.CRAWLER_TIMEOUT,
                max_depth=self.CRAWLER_MAX_DEPTH,
                concurrency=self.CRAWLER_CONCURRENCY,
                headless=self.CRAWLER_HEADLESS,
                allowed_resolutions=self._parse_csv_tuple(self.CRAWLER_ALLOWED_RESOLUTIONS),
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

    def update_qbit(self, host: str | None = None, username: str | None = None, password: str | None = None):
        """动态更新 qB 配置（由前端配置面板调用）

        返回:
            True — 更新成功
            str  — 错误信息（验证失败）
        """
        if host is not None:
            host = host.strip()
            if not host:
                return "qBittorrent 主机地址不能为空"
            if not (host.startswith("http://") or host.startswith("https://")):
                return f"非法的 qBittorrent 主机地址: {host}（必须以 http:// 或 https:// 开头）"
            self.QBIT_HOST = host

        if username is not None:
            username = username.strip()
            if not username:
                return "用户名不能为空"
            self.QBIT_USERNAME = username

        if password is not None:
            password = password.strip()
            if not password:
                return "密码不能为空"
            self.QBIT_PASSWORD = password

        self._qbit_config = None  # 下次调用 .qbit 时重建
        return True

    @staticmethod
    def _parse_csv_tuple(value: str) -> tuple[str, ...]:
        values = tuple(item.strip() for item in value.split(",") if item.strip())
        return values or ("2160p", "4k")


settings = Settings()
