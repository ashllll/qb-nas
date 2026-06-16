"""qBittorrent WebAPI v2 客户端 v2.0"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from magnet_harvester.config import QBitConfig
from magnet_harvester.qbit_client._transport import QBitTransport
from magnet_harvester.qbit_client.mapper import TorrentStatusMapper
from magnet_harvester.qbit_client.paths import QBitPathResolver, _safe_fs_segment
from magnet_harvester.qbit_client.stats import QBittorrentStats

log = logging.getLogger(__name__)

QBitApiObject = dict[str, object]


class QBittorrentClient:
    def __init__(self, config: QBitConfig):
        self._config = config
        self.host = self._config.host.rstrip("/")
        self.username = self._config.username
        self.password = self._config.password
        self.stats = QBittorrentStats()
        self._transport = QBitTransport(
            host=self.host,
            username=self.username,
            password=self.password,
            stats=self.stats,
        )
        self._ping_cache_ttl = 5.0
        self._last_ping_at = 0.0
        self._last_ping_result: bool | None = None
        self._cached_default_path: str | None = None
        self._category_locks: dict[str, asyncio.Lock] = {}
        self.last_error: str | None = None
        self._maindata_rid = 0
        self._torrent_snapshot: Dict[str, dict] = {}
        self._recently_removed: set[str] = set()
        self._path_resolver = QBitPathResolver(
            get_categories=self.get_categories,
            get_torrents=self._get_torrents_list,
        )

    @property
    def _client(self):
        return self._transport._client

    @_client.setter
    def _client(self, value):
        self._transport._client = value

    async def close(self):
        await self._transport.close()

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        return await self._transport.request(method, path, **kw)

    async def ping(self) -> bool:
        now = time.time()
        if self._last_ping_result is not None and now - self._last_ping_at < self._ping_cache_ttl:
            return self._last_ping_result
        try:
            r = await self._req("GET", "/app/version")
            ok = r.status_code == 200
        except Exception as e:
            log.warning(f"qBittorrent ping 失败: {e}")
            ok = False
        self._last_ping_at = time.time()
        self._last_ping_result = ok
        return ok

    async def get_maindata(self, rid: int = 0) -> QBitApiObject:
        try:
            r = await self._req("GET", f"/sync/maindata?rid={rid}")
            if r.status_code == 200:
                return r.json()
            return {}
        except Exception as e:
            log.warning(f"get_maindata 失败: {e}")
            return {}

    async def poll_torrent_snapshot(self) -> Dict[str, dict]:
        """增量同步 qB torrent 状态，并缓存当前快照。"""
        data = await self.get_maindata(rid=self._maindata_rid)
        if not data:
            return dict(self._torrent_snapshot)

        self._maindata_rid = data.get("rid", self._maindata_rid)

        torrents = data.get("torrents", {}) or {}
        for hash_key, info in torrents.items():
            self._torrent_snapshot[hash_key.lower()] = info

        removed = {str(h).lower() for h in data.get("torrents_removed", [])}
        if removed:
            self._recently_removed = removed
            for hash_key in removed:
                self._torrent_snapshot.pop(hash_key, None)
        else:
            self._recently_removed = set()

        return dict(self._torrent_snapshot)

    def take_recently_removed(self) -> set[str]:
        removed = set(self._recently_removed)
        self._recently_removed.clear()
        return removed

    @staticmethod
    def map_torrent_status(torrent: dict) -> dict:
        return TorrentStatusMapper.map(torrent)

    async def get_torrent_properties(self, hash: str) -> QBitApiObject:
        try:
            r = await self._req("GET", f"/torrents/properties?hash={hash}")
            if r.status_code == 200:
                return r.json()
            return {}
        except Exception:
            return {}

    async def get_categories(self) -> dict:
        try:
            r = await self._req("GET", "/torrents/categories")
            if r.status_code != 200:
                log.warning(f"get_categories 返回 {r.status_code}")
                return {}
            return r.json()
        except Exception as e:
            log.warning(f"get_categories 异常: {e}")
            return {}

    async def _find_torrent_by_prefix(self, hash_prefix: str) -> dict | None:
        """在 qB 种子列表中查找 hash 前缀匹配的种子（去重检测）。"""
        try:
            r = await self._req("GET", "/torrents/info")
            if r.status_code != 200:
                return None
            torrents = r.json()
            for t in torrents:
                if t.get("hash", "").startswith(hash_prefix):
                    return t
        except Exception:
            pass
        return None

    async def _get_torrents_list(self) -> list:
        """辅助方法：获取种子列表（供 QBitPathResolver 使用）"""
        try:
            r = await self._req("GET", "/torrents/info")
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return []

    async def get_default_save_path(self) -> str | None:
        """获取 qBittorrent 默认保存路径（缓存）。

        飞牛 NAS 的 Docker 版 qB 中，/app/defaultSavePath 返回容器内部路径
        （如 /var/apps/qBittorrent/.../Download），而非 NAS 真实路径。
        因此优先从已存在的分类 / torrent 的 savePath 获取真实路径。
        """
        if self._cached_default_path:
            return self._cached_default_path

        # 1-2. 从已有分类/种子推断（通过 QBitPathResolver）
        path = await self._path_resolver.resolve()
        if path:
            self._cached_default_path = path
            return path

        # 3. 兜底：/app/defaultSavePath（Docker 下可能返回容器内部路径）
        try:
            r = await self._req("GET", "/app/defaultSavePath")
            if r.status_code == 200:
                path = r.text.strip()
                if path:
                    self._cached_default_path = path
                    log.warning(
                        f"未找到已有分类或种子，使用 qB 默认路径: {path}"
                        f"（Docker 版可能返回容器内部路径而非 NAS 路径）"
                    )
                    return path
        except Exception as e:
            log.warning(f"get_default_save_path 异常: {e}")

        return None

    def clear_cached_path(self):
        """清除缓存的路径，强制下次重新检测（/api/config PUT 时调用）"""
        self._cached_default_path = None
        self._path_resolver.clear_cache()

    async def get_base_save_path(self) -> str:
        """获取基础保存路径"""
        return await self.get_default_save_path() or "/volume1/downloads"

    async def ensure_category(self, name: str, save_path: str, max_retries: int = 2):
        # 对同一分类串行化，防止并发创建/编辑竞态
        lock = self._category_locks.setdefault(name, asyncio.Lock())
        async with lock:
            for attempt in range(max_retries):
                try:
                    cats = await self.get_categories()

                    if name not in cats:
                        await self._req("POST", "/torrents/createCategory",
                                        data={"category": name, "savePath": save_path})
                        log.info(f"创建分类: [{name}] → {save_path}")
                        await asyncio.sleep(0.5)
                        cats = await self.get_categories()

                    if name in cats and cats[name].get("savePath", "") != save_path:
                        await self._req("POST", "/torrents/editCategory",
                                        data={"category": name, "savePath": save_path})
                        log.info(f"更新分类路径: [{name}] → {save_path}")

                    return True

                except Exception as e:
                    log.warning(f"ensure_category 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)

            return False


    async def add_magnet(self, magnet: str, category: str, save_path: str = "") -> bool:
        """添加磁力链接到 qBittorrent。

        按照 qB 官方 API 最佳实践：
        - 先 ensure_category(category, save_path) 确保分类存在
        - add 时不传 savepath，让 qB 根据分类的 savePath 自动路由
        - autoTMM=true 让 qB 自动管理分类目录
        """
        self.stats.total_added += 1

        # 0. 校验磁力格式
        btih_match = re.search(r'btih:([A-Za-z0-9]{8,40})', magnet)
        if not btih_match:
            self.last_error = "磁力链接格式无效（缺少 btih）"
            self.stats.total_failed += 1
            self.stats.consecutive_failures += 1
            self.stats.last_failure_time = time.time()
            return False
        btih_prefix = btih_match.group(1)[:8]

        # 1. 确保分类在 qB 中存在（自动创建不存在的分类）
        category_save_path = save_path
        if category_save_path and not category_save_path.startswith("/"):
            base = await self.get_base_save_path()
            if base:
                category_save_path = f"{base}/{category_save_path}"
        # 即使无法拼接完整路径，也传入分类名让 ensure_category 至少创建分类
        if not category_save_path:
            category_save_path = save_path  # 保留原始值（如 "SexArt"），qB 可据此创建

        # 2. 如果配置了 FS_BASE_PATH，先创建真实目录（qB 的 createCategory 不是 mkdir）
        fs_base = self._config.fs_base_path.strip()
        if fs_base:
            (Path(fs_base) / _safe_fs_segment(category)).mkdir(parents=True, exist_ok=True)

        try:
            # 2. 确保分类存在且路径正确
            if category_save_path:
                category_ok = await self.ensure_category(category, category_save_path)
                if not category_ok:
                    log.warning(f"分类 [{category}] 创建失败")

            # 3. 添加任务 — 只传 category + autoTMM，不传 savepath
            r = await self._req("POST", "/torrents/add", data={
                "urls":     magnet,
                "category": category,
                "use_auto_torrent_management":  "true",
            })

            ok = r.text.strip() == "Ok."

            if ok:
                self.stats.total_success += 1
                self.stats.consecutive_failures = 0
                self.stats.last_success_time = time.time()
                log.debug(f"添加种子成功: {category}")
            else:
                # qB 返回 Fails. — 检查是否种子已存在（重复添加）
                existing = await self._find_torrent_by_prefix(btih_prefix)
                if existing:
                    log.info(f"种子已存在于 qB (btih:{btih_prefix}…)，跳过: {existing.get('name', '?')[:40]}")
                    self.stats.total_success += 1
                    self.stats.consecutive_failures = 0
                    return True

                self.last_error = f"qB 拒绝 (btih:{btih_prefix}…) — {r.text.strip()[:100]}"
                self.stats.total_failed += 1
                self.stats.consecutive_failures += 1
                self.stats.last_failure_time = time.time()
                log.warning(f"add_magnet 失败: {self.last_error}")

            return ok

        except Exception as e:
            self.last_error = str(e)
            self.stats.total_failed += 1
            self.stats.consecutive_failures += 1
            self.stats.last_failure_time = time.time()
            log.error(f"add_magnet 异常: {e}")
            return False

    async def get_torrents(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        hashes: Optional[List[str]] = None,
    ) -> List[dict]:
        try:
            params = {}
            if category:
                params["category"] = category
            if status:
                params["status"] = status
            if hashes:
                params["hashes"] = "|".join(hashes)
            
            r = await self._req("GET", "/torrents/info", params=params)
            
            if r.status_code == 200:
                return r.json()
            return []
        except Exception:
            return []

    async def delete_torrent(self, hashes: List[str], delete_files: bool = False) -> bool:
        try:
            data = {
                "hashes": "|".join(hashes),
                "deleteFiles": "true" if delete_files else "false",
            }
            r = await self._req("POST", "/torrents/delete", data=data)
            return r.text.strip() == "Ok."
        except Exception as e:
            log.error(f"delete_torrent 失败: {e}")
            return False

    async def recheck_torrent(self, hashes: List[str]) -> bool:
        try:
            data = {"hashes": "|".join(hashes)}
            r = await self._req("POST", "/torrents/recheck", data=data)
            return r.text.strip() == "Ok."
        except Exception:
            return False

    async def get_transfer_info(self) -> QBitApiObject:
        try:
            r = await self._req("GET", "/transfer/info")
            if r.status_code == 200:
                return r.json()
            return {}
        except Exception:
            return {}

    def get_stats(self) -> dict:
        return self.stats.as_dict()

    def is_healthy(self) -> bool:
        return self.stats.consecutive_failures < 3
