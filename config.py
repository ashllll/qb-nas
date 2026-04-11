from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Dict, Optional

from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    QBIT_HOST: str = "http://192.168.1.100:8080"
    QBIT_USERNAME: str = "admin"
    QBIT_PASSWORD: str = "adminadmin"

    MINIMAX_API_KEY: str = "your-minimax-api-key"
    MINIMAX_MODEL: str = "MiniMax-M2.5-highspeed"
    MINIMAX_THINKING_MODEL: str = "MiniMax-M2.5"
    THINKING_RECHECK: bool = True

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
        self._path_cache: Dict[str, Path] = {}
        self._validate_paths_on_init()

    def _validate_paths_on_init(self):
        for category, path_str in self.CATEGORY_PATHS.items():
            path = Path(path_str)
            
            try:
                if not path.exists():
                    if self.AUTO_CREATE_DIRS:
                        path.mkdir(parents=True, exist_ok=True)
                        
                        if path.exists():
                            log.info(f"✅ 目录创建成功: {category} -> {path_str}")
                            
                            if not os.access(path, os.W_OK):
                                log.warning(f"⚠️ 目录无写入权限: {path_str}")
                            else:
                                log.debug(f"目录权限正常: {path_str}")
                        else:
                            log.error(f"❌ 目录创建失败（mkdir 返回成功但目录不存在）: {path_str}")
                    else:
                        log.warning(f"⚠️ 目录不存在: {category} -> {path_str}")
                elif not os.access(path, os.W_OK):
                    log.warning(f"⚠️ 目录无写入权限: {path_str}")
                else:
                    log.debug(f"✅ 目录验证通过: {category} -> {path_str}")
                    
            except PermissionError as e:
                log.error(f"❌ 权限不足，无法创建目录: {path_str} - {e}")
            except FileNotFoundError as e:
                log.error(f"❌ 上级目录不存在，无法创建: {path_str} - {e}")
            except OSError as e:
                log.error(f"❌ 系统错误，创建目录失败: {path_str} - {e}")
            except Exception as e:
                log.error(f"❌ 未知错误，目录操作失败: {path_str} - {type(e).__name__}: {e}")

    @property
    def CATEGORY_PATHS(self) -> dict[str, str]:
        return {
            "电影": self.PATH_MOVIE,
            "电视剧": self.PATH_TV,
            "动漫": self.PATH_ANIME,
            "音乐": self.PATH_MUSIC,
            "游戏": self.PATH_GAME,
            "软件": self.PATH_SOFTWARE,
            "综艺": self.PATH_VARIETY,
            "纪录片": self.PATH_DOCUMENTARY,
            "其他": self.PATH_OTHER,
        }

    def get_category_path(self, category: str, template_vars: Optional[Dict[str, str]] = None) -> str:
        base_path = self.CATEGORY_PATHS.get(category, self.PATH_OTHER)
        
        if not self.PATH_TEMPLATE_ENABLED or not template_vars:
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
            if self.AUTO_CREATE_DIRS:
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
            target = Path(self.PATH_MOVIE).anchor

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
                "healthy": free_gb >= self.MIN_DISK_SPACE_GB,
                "warning": free_gb < self.MIN_DISK_SPACE_GB * 2,
            }
        except Exception as e:
            log.error(f"检查磁盘空间失败: {e}")
            return {
                "total_gb": 0,
                "used_gb": 0,
                "free_gb": 0,
                "used_percent": 0,
                "healthy": True,
                "warning": False,
                "error": str(e),
            }

    def get_category_stats(self) -> Dict[str, dict]:
        stats = {}
        for category, path_str in self.CATEGORY_PATHS.items():
            path = Path(path_str)
            
            try:
                if path.exists():
                    items = list(path.iterdir())
                    size_bytes = sum(
                        item.stat().st_size for item in items if item.is_file()
                    )
                    
                    stats[category] = {
                        "path": path_str,
                        "exists": True,
                        "writable": os.access(path, os.W_OK),
                        "item_count": len(items),
                        "size_gb": round(size_bytes / (1024 ** 3), 2),
                    }
                else:
                    stats[category] = {
                        "path": path_str,
                        "exists": False,
                        "writable": False,
                        "item_count": 0,
                        "size_gb": 0,
                    }
            except Exception as e:
                stats[category] = {
                    "path": path_str,
                    "exists": True,
                    "writable": False,
                    "item_count": 0,
                    "size_gb": 0,
                    "error": str(e),
                }
        
        return stats


settings = Settings()

