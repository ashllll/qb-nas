from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════
# 按关注点拆分的子配置（窄接口，每个模块只依赖自己需要的）
# ═══════════════════════════════════════════════════

@dataclass
class CrawlerConfig:
    """爬虫配置 — 只给 crawler.py 用"""
    timeout: int = 30
    max_depth: int = 2
    concurrency: int = 3
    headless: bool = True


@dataclass
class QBitConfig:
    """qBittorrent 配置 — 只给 qbit_client.py 用"""
    host: str = "http://192.168.1.100:8080"
    username: str = "admin"
    password: str = "adminadmin"


@dataclass
class ClassifierConfig:
    """Agent 配置 — 供 agent.py 使用"""
    api_key: str = "your-minimax-api-key"
    model: str = "MiniMax-M2.5-highspeed"


@dataclass
class TTSConfig:
    """TTS 配置 — 只给 tts_client.py 用"""
    enabled: bool = True
    api_key: str = "your-minimax-api-key"


@dataclass
class ServiceConfig:
    """服务配置 — 只给 main.py 用"""
    host: str = "0.0.0.0"
    port: int = 8899


class PathConfig:
    """路径配置 — 管理下载目录映射、模板、磁盘检查。

    注意：这不是 dataclass，因为包含业务逻辑（路径验证、磁盘检查）。
    """

    def __init__(self, paths: Optional[Dict[str, str]] = None, **kwargs):
        self._paths: Dict[str, str] = paths or {}
        self._path_cache: Dict[str, Path] = {}
        self.min_disk_space_gb: float = kwargs.get("min_disk_space_gb", 10.0)
        self.auto_create_dirs: bool = kwargs.get("auto_create_dirs", True)
        self.template_enabled: bool = kwargs.get("template_enabled", True)

    @property
    def category_paths(self) -> Dict[str, str]:
        return dict(self._paths)

    def get_category_path(self, category: str, template_vars: Optional[Dict[str, str]] = None) -> str:
        base_path = self._paths.get(category, self._paths.get("其他", ""))
        if not self.template_enabled or not template_vars:
            return base_path
        try:
            result = base_path
            for key, value in template_vars.items():
                result = result.replace(f"{{{key}}}", value)
                result = result.replace(f"${key}", value)
            return result
        except Exception as e:
            log.warning(f"路径模板解析失败 [{base_path}]: {e}")
            return base_path

    def validate_path(self, path_str: str) -> tuple[bool, str]:
        path = Path(path_str)
        if not path.exists():
            if self.auto_create_dirs:
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    return True, "已创建目录"
                except Exception as e:
                    return False, f"无法创建目录: {e}"
            return False, "目录不存在"
        if not path.is_dir():
            return False, "路径不是目录"
        if not os.access(path, os.W_OK):
            return False, "目录无写入权限"
        return True, "路径有效"

    def check_disk_space(self, path_str: Optional[str] = None) -> dict:
        if path_str:
            target = Path(path_str)
        else:
            target = Path(self._paths.get("电影", "/")).anchor
        try:
            usage = shutil.disk_usage(target)
            total_gb = usage.total / (1024 ** 3)
            used_gb = usage.used / (1024 ** 3)
            free_gb = usage.free / (1024 ** 3)
            percent = (usage.used / usage.total * 100) if usage.total > 0 else 0
            return {
                "total_gb": round(total_gb, 2),
                "used_gb": round(used_gb, 2),
                "free_gb": round(free_gb, 2),
                "used_percent": round(percent, 1),
                "healthy": free_gb >= self.min_disk_space_gb,
                "warning": free_gb < self.min_disk_space_gb * 2,
            }
        except Exception as e:
            log.error(f"检查磁盘空间失败: {e}")
            return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "used_percent": 0,
                    "healthy": True, "warning": False, "error": str(e)}

    def get_category_stats(self) -> Dict[str, dict]:
        stats = {}
        for category, path_str in self._paths.items():
            path = Path(path_str)
            try:
                if path.exists():
                    items = list(path.iterdir())
                    size_bytes = sum(item.stat().st_size for item in items if item.is_file())
                    stats[category] = {"path": path_str, "exists": True,
                                       "writable": os.access(path, os.W_OK),
                                       "item_count": len(items),
                                       "size_gb": round(size_bytes / (1024 ** 3), 2)}
                else:
                    stats[category] = {"path": path_str, "exists": False,
                                       "writable": False, "item_count": 0, "size_gb": 0}
            except Exception as e:
                stats[category] = {"path": path_str, "exists": True, "writable": False,
                                   "item_count": 0, "size_gb": 0, "error": str(e)}
        return stats

    def validate_all(self):
        """启动时验证所有路径"""
        for category, path_str in self._paths.items():
            path = Path(path_str)
            try:
                if not path.exists():
                    if self.auto_create_dirs:
                        path.mkdir(parents=True, exist_ok=True)
                        if path.exists():
                            log.info(f"✅ 目录创建成功: {category} -> {path_str}")
                            if not os.access(path, os.W_OK):
                                log.warning(f"⚠️ 目录无写入权限: {path_str}")
                            else:
                                log.debug(f"目录权限正常: {path_str}")
                        else:
                            log.error(f"❌ 目录创建失败: {path_str}")
                    else:
                        log.warning(f"⚠️ 目录不存在: {category} -> {path_str}")
                elif not os.access(path, os.W_OK):
                    log.warning(f"⚠️ 目录无写入权限: {path_str}")
                else:
                    log.debug(f"✅ 目录验证通过: {category} -> {path_str}")
            except PermissionError as e:
                log.error(f"❌ 权限不足: {path_str} - {e}")
            except FileNotFoundError as e:
                log.error(f"❌ 上级目录不存在: {path_str} - {e}")
            except OSError as e:
                log.error(f"❌ 系统错误: {path_str} - {e}")
            except Exception as e:
                log.error(f"❌ 未知错误: {path_str} - {type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════
# Settings — 统一入口（.env 加载 + 拆分子配置）
# ═══════════════════════════════════════════════════

class Settings(BaseSettings):
    """向后兼容的统一设置入口。

    从 .env 加载所有字段，通过 factory 方法创建子配置。
    """

    QBIT_HOST: str = "http://192.168.1.100:8080"
    QBIT_USERNAME: str = "admin"
    QBIT_PASSWORD: str = "adminadmin"

    MINIMAX_API_KEY: str = "your-minimax-api-key"
    MINIMAX_MODEL: str = "MiniMax-M2.5-highspeed"

    TTS_ENABLED: bool = True

    SERVICE_HOST: str = "0.0.0.0"
    SERVICE_PORT: int = 8899

    CRAWLER_TIMEOUT: int = 30
    CRAWLER_MAX_DEPTH: int = 2
    CRAWLER_CONCURRENCY: int = 3
    CRAWLER_HEADLESS: bool = True

    PATH_MOVIE: str = "/volume1/downloads/movies"
    PATH_TV: str = "/volume1/downloads/tv"
    PATH_ANIME: str = "/volume1/downloads/anime"
    PATH_MUSIC: str = "/volume1/downloads/music"
    PATH_GAME: str = "/volume1/downloads/games"
    PATH_SOFTWARE: str = "/volume1/downloads/software"
    PATH_VARIETY: str = "/volume1/downloads/variety"
    PATH_DOCUMENTARY: str = "/volume1/downloads/documentary"
    PATH_OTHER: str = "/volume1/downloads/others"

    MIN_DISK_SPACE_GB: float = 10.0
    AUTO_CREATE_DIRS: bool = True
    PATH_TEMPLATE_ENABLED: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._path_config: Optional[PathConfig] = None
        self._crawler_config: Optional[CrawlerConfig] = None
        self._qbit_config: Optional[QBitConfig] = None
        self._classifier_config: Optional[ClassifierConfig] = None
        self._tts_config: Optional[TTSConfig] = None
        self._service_config: Optional[ServiceConfig] = None

    # ── 子配置工厂 ──────────────────────────────

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
    def qbit(self) -> QBitConfig:
        if self._qbit_config is None:
            self._qbit_config = QBitConfig(
                host=self.QBIT_HOST,
                username=self.QBIT_USERNAME,
                password=self.QBIT_PASSWORD,
            )
        return self._qbit_config

    @property
    def classifier(self) -> ClassifierConfig:
        if self._classifier_config is None:
            self._classifier_config = ClassifierConfig(
                api_key=self.MINIMAX_API_KEY,
                model=self.MINIMAX_MODEL,
            )
        return self._classifier_config

    @property
    def tts(self) -> TTSConfig:
        if self._tts_config is None:
            self._tts_config = TTSConfig(
                enabled=self.TTS_ENABLED,
                api_key=self.MINIMAX_API_KEY,
            )
        return self._tts_config

    @property
    def service(self) -> ServiceConfig:
        if self._service_config is None:
            self._service_config = ServiceConfig(
                host=self.SERVICE_HOST,
                port=self.SERVICE_PORT,
            )
        return self._service_config

    @property
    def paths(self) -> PathConfig:
        if self._path_config is None:
            self._path_config = PathConfig(
                paths={
                    "电影": self.PATH_MOVIE,
                    "电视剧": self.PATH_TV,
                    "动漫": self.PATH_ANIME,
                    "音乐": self.PATH_MUSIC,
                    "游戏": self.PATH_GAME,
                    "软件": self.PATH_SOFTWARE,
                    "综艺": self.PATH_VARIETY,
                    "纪录片": self.PATH_DOCUMENTARY,
                    "其他": self.PATH_OTHER,
                },
                min_disk_space_gb=self.MIN_DISK_SPACE_GB,
                auto_create_dirs=self.AUTO_CREATE_DIRS,
                template_enabled=self.PATH_TEMPLATE_ENABLED,
            )
            # 启动时立即验证路径
            self._path_config.validate_all()
        return self._path_config

    # ── 向后兼容属性 ──────────────────────────────

    @property
    def CATEGORY_PATHS(self) -> dict[str, str]:
        """向后兼容 — classifier.py / main.py 使用"""
        return self.paths.category_paths

    def get_category_path(self, category: str, template_vars=None) -> str:
        """向后兼容"""
        return self.paths.get_category_path(category, template_vars)

    def validate_path(self, path_str: str) -> tuple[bool, str]:
        """向后兼容"""
        return self.paths.validate_path(path_str)

    def check_disk_space(self, path_str: Optional[str] = None) -> dict:
        """向后兼容"""
        return self.paths.check_disk_space(path_str)

    def get_category_stats(self) -> Dict[str, dict]:
        """向后兼容"""
        return self.paths.get_category_stats()


settings = Settings()
