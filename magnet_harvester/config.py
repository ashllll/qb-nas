from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)


# ── 子配置 ──────────────────────────────

@dataclass
class CrawlerConfig:
    timeout: int = 30
    max_depth: int = 2
    concurrency: int = 3
    headless: bool = True


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

    FS_BASE_PATH: str = ""  # 脚本可创建目录的真实路径（如 Z:\downloads），为空则跳过 mkdir

    MIN_DISK_SPACE_GB: float = 10.0
    AUTO_CREATE_DIRS: bool = True

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
        """动态更新 qB 配置（由前端配置面板调用）"""
        if host:
            self.QBIT_HOST = host
        if username:
            self.QBIT_USERNAME = username
        if password:
            self.QBIT_PASSWORD = password
        self._qbit_config = None  # 下次调用 .qbit 时重建


settings = Settings()
