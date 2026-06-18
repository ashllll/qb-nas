"""QBitPathResolver — qBittorrent 路径解析与安全处理

纯逻辑模块，不依赖 HTTP 客户端。通过回调函数获取外部数据。
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Awaitable, Optional

log = logging.getLogger(__name__)


def _safe_fs_segment(name: str) -> str:
    """把分类名压成单个本地路径段，避免 FS_BASE_PATH 下目录穿越。"""
    safe = re.sub(r"[\\/:\0]+", "_", name).strip().strip(".")
    safe = re.sub(r"\s+", " ", safe)
    return safe or "uncategorized"


def _extract_base_from_path(save_path: str) -> Optional[str]:
    """从分类/种子的 savePath 提取基础下载路径（排除 Docker 内部路径）。

    路径格式如 /vol2/1000/downloads/电影，取父目录。
    单层路径如 /downloads 没有可推断的父级，返回 None。
    """
    if not save_path or save_path.startswith("/var/"):
        return None
    stripped = save_path.strip("/")
    if "/" in stripped:
        return "/" + "/".join(stripped.split("/")[:-1])
    return None


class QBitPathResolver:
    """解析 qBittorrent 的基础保存路径，支持缓存。

    依赖（通过构造函数注入）:
        get_categories: 异步获取分类列表 → dict[str, dict]
        get_torrents:   异步获取种子列表 → list[dict]
    """

    def __init__(
        self,
        get_categories: Callable[[], Awaitable[dict]],
        get_torrents: Callable[[], Awaitable[list]],
    ):
        self._get_categories = get_categories
        self._get_torrents = get_torrents
        self._cached: Optional[str] = None

    def clear_cache(self):
        """清除缓存的路径，强制下次重新检测。"""
        self._cached = None

    async def resolve(self) -> Optional[str]:
        """解析基础保存路径，优先从已有分类/种子推断。"""
        if self._cached:
            return self._cached

        # 1. 从已有分类获取
        path = await self._from_categories()
        if path:
            self._cached = path
            return path

        # 2. 从已有种子获取
        path = await self._from_torrents()
        if path:
            self._cached = path
            return path

        return None

    async def _from_categories(self) -> Optional[str]:
        try:
            cats = await self._get_categories()
            for name, info in cats.items():
                base = _extract_base_from_path(info.get("savePath", ""))
                if base:
                    log.info(f"基础路径（从分类 [{name}]）: {base}")
                    return base
        except Exception as e:
            log.debug("从分类获取基础路径异常: %s", e)
        return None

    async def _from_torrents(self) -> Optional[str]:
        try:
            torrents = await self._get_torrents()
            for t in torrents:
                base = _extract_base_from_path(t.get("save_path", ""))
                if base:
                    log.info(f"基础路径（从种子 [{t.get('name', '')[:20]}…]）: {base}")
                    return base
        except Exception as e:
            log.debug("从种子获取基础路径异常: %s", e)
        return None
