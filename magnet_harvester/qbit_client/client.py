"""qBittorrent WebAPI v2 客户端 v2.0"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Dict, List, Optional

import httpx

from magnet_harvester.config import QBitConfig
from magnet_harvester.qbit_client._transport import QBitTransport
from magnet_harvester.qbit_client.mapper import TorrentStatusMapper
from magnet_harvester.qbit_client.paths import QBitPathResolver
from magnet_harvester.qbit_client.stats import QBittorrentStats
from magnet_harvester.qbit_client.submitter import MagnetSubmitter
from magnet_harvester.qbit_client.sync_state import QBitSyncState

log = logging.getLogger(__name__)

QBitApiObject = dict[str, object]


class _ClientSubmissionGateway:
    """Adapter from QBittorrentClient internals to MagnetSubmitter's gateway."""

    def __init__(self, client: "QBittorrentClient"):
        self._client = client

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        return await self._client._req(method, path, **kw)

    async def ensure_category(self, name: str, save_path: str) -> bool:
        return await self._client.ensure_category(name, save_path)

    async def get_base_save_path(self) -> str:
        return await self._client.get_base_save_path()

    async def find_torrent_by_prefix(self, hash_prefix: str) -> dict | None:
        return await self._client._find_torrent_by_prefix(hash_prefix)


class _ClientSubmissionRecorder:
    """Adapter from submission outcomes to QBittorrentClient stats fields."""

    def __init__(self, client: "QBittorrentClient"):
        self._client = client

    def attempted(self) -> None:
        self._client.stats.total_added += 1

    def succeeded(self) -> None:
        self._client.stats.total_success += 1
        self._client.stats.consecutive_failures = 0
        self._client.stats.last_success_time = time.time()

    def failed(self) -> None:
        self._client.stats.total_failed += 1
        self._client.stats.consecutive_failures += 1
        self._client.stats.last_failure_time = time.time()

    def error(self, message: str | None) -> None:
        self._client.last_error = message


class QBittorrentClient:
    MAX_CATEGORY_LOCKS = 200  # 分类锁上限（qB 本身限制约 100 个）

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
        self._ping_lock = asyncio.Lock()
        self._cached_default_path: str | None = None
        # LRU 有界字典，防止异常/恶意分类名导致无限增长（上限 200）
        self._category_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self.last_error: str | None = None
        self._sync_state = QBitSyncState()
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
        now = time.monotonic()
        if self._last_ping_result is not None and now - self._last_ping_at < self._ping_cache_ttl:
            return self._last_ping_result
        async with self._ping_lock:
            # 双重检查：获取锁期间缓存可能已被另一个协程填充
            now = time.monotonic()
            if self._last_ping_result is not None and now - self._last_ping_at < self._ping_cache_ttl:
                return self._last_ping_result
            try:
                r = await self._req("GET", "/app/version")
                ok = r.status_code == 200
            except Exception as e:
                log.warning(f"qBittorrent ping 失败: {e}")
                ok = False
            self._last_ping_at = time.monotonic()
            self._last_ping_result = ok
            return ok

    async def get_maindata(self, rid: int = 0) -> QBitApiObject:
        try:
            r = await self._req("GET", f"/sync/maindata?rid={rid}")
            if r.status_code == 200:
                return r.json()
            log.warning(f"get_maindata 返回非 200: {r.status_code}")
            return {}
        except httpx.TransportError as e:
            log.error(f"get_maindata 网络异常: {e}")
            return {}
        except Exception as e:
            log.error(f"get_maindata 未知异常: {e}", exc_info=True)
            return {}

    async def poll_torrent_snapshot(self) -> Dict[str, dict]:
        """增量同步 qB torrent 状态，并缓存当前快照。"""
        return await self._sync_state.poll(self.get_maindata)

    def take_recently_removed(self) -> set[str]:
        return self._sync_state.take_recently_removed()

    @staticmethod
    def map_torrent_status(torrent: dict) -> dict:
        return TorrentStatusMapper.map(torrent)

    async def get_torrent_properties(self, hash: str) -> QBitApiObject:
        try:
            r = await self._req("GET", f"/torrents/properties?hash={hash}")
            if r.status_code == 200:
                return r.json()
            return {}
        except Exception as e:
            log.warning("get_torrent_properties 异常 hash=%s: %s", hash, e)
            return {}

    async def get_categories(self) -> dict:
        try:
            r = await self._req("GET", "/torrents/categories")
            if r.status_code != 200:
                log.warning(f"get_categories 返回 {r.status_code}")
                return {}
            return r.json()
        except httpx.TransportError as e:
            log.error(f"get_categories 网络异常: {e}")
            return {}
        except Exception as e:
            log.error(f"get_categories 未知异常: {e}", exc_info=True)
            return {}

    async def _find_torrent_by_prefix(self, hash_prefix: str) -> dict | None:
        """在 qB 种子列表中查找 hash 前缀匹配的种子（去重检测）。"""
        try:
            r = await self._req("GET", "/torrents/info")
            if r.status_code != 200:
                return None
            torrents = r.json()
            prefix_lower = hash_prefix.lower()
            for t in torrents:
                if t.get("hash", "").lower().startswith(prefix_lower):
                    return t
        except Exception as e:
            log.debug("按前缀查找 torrent 异常: %s", e)
        return None

    async def _get_torrents_list(self) -> list:
        """辅助方法：获取种子列表（供 QBitPathResolver 使用）"""
        try:
            r = await self._req("GET", "/torrents/info")
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            log.debug("获取种子列表异常: %s", e)
        return []

    async def get_default_save_path(self) -> str | None:
        """获取 qBittorrent 默认保存路径（缓存）。

        飞牛 NAS 的 Docker 版 qB 中，/app/defaultSavePath 返回容器内部路径
        （如 /var/apps/qBittorrent/.../Download），而非 NAS 真实路径。
        因此优先从已存在的分类 / torrent 的 savePath 获取真实路径。
        """
        if self._cached_default_path is not None:
            return self._cached_default_path or None

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

        self._cached_default_path = ""  # 负缓存：避免重复网络请求
        return None

    def clear_cached_path(self):
        """清除缓存的路径，强制下次重新检测（/api/config PUT 时调用）"""
        self._cached_default_path = None
        self._path_resolver.clear_cache()

    async def get_base_save_path(self) -> str:
        """获取基础保存路径"""
        return await self.get_default_save_path() or "/volume1/downloads"

    async def ensure_category(self, name: str, save_path: str, max_retries: int = 2):
        # 对同一分类串行化，防止并发创建/编辑竞态；LRU 清理防无限增长
        if name not in self._category_locks:
            if len(self._category_locks) >= self.MAX_CATEGORY_LOCKS:
                self._category_locks.popitem(last=False)
            self._category_locks[name] = asyncio.Lock()
        self._category_locks.move_to_end(name)
        lock = self._category_locks[name]
        async with lock:
            for attempt in range(max_retries):
                try:
                    cats = await self.get_categories()

                    if name not in cats:
                        await self._req(
                            "POST",
                            "/torrents/createCategory",
                            data={"category": name, "savePath": save_path},
                        )
                        log.info(f"创建分类: [{name}] → {save_path}")
                        cats = await self._wait_for_category(name)
                        if name not in cats:
                            log.warning("分类 [%s] 创建后未出现在 qB 分类列表中", name)
                            return False

                    if name in cats and cats[name].get("savePath", "") != save_path:
                        await self._req(
                            "POST",
                            "/torrents/editCategory",
                            data={"category": name, "savePath": save_path},
                        )
                        log.info(f"更新分类路径: [{name}] → {save_path}")

                    return True

                except Exception as e:
                    log.warning(f"ensure_category 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)

            return False

    async def _wait_for_category(
        self,
        name: str,
        *,
        checks: int = 15,
        interval: float = 0.2,
    ) -> dict:
        """Poll qB until a newly created category becomes visible."""
        cats = {}
        for _ in range(checks):
            await asyncio.sleep(interval)
            cats = await self.get_categories()
            if name in cats:
                return cats
        return cats

    async def add_magnet(self, magnet: str, category: str, save_path: str = "") -> bool:
        """添加磁力链接到 qBittorrent。

        按照 qB 官方 API 最佳实践：
        - 先 ensure_category(category, save_path) 确保分类存在
        - add 时不传 savepath，让 qB 根据分类的 savePath 自动路由
        - autoTMM=true 让 qB 自动管理分类目录
        """
        submitter = MagnetSubmitter(
            gateway=_ClientSubmissionGateway(self),
            fs_base_path=self._config.fs_base_path,
            recorder=_ClientSubmissionRecorder(self),
        )
        return await submitter.add_magnet(magnet, category, save_path)

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
        except Exception as e:
            log.warning("获取 tracker 列表异常: %s", e)
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
        except Exception as e:
            log.warning("recheck torrent 异常: %s", e)
            return False

    async def get_transfer_info(self) -> QBitApiObject:
        try:
            r = await self._req("GET", "/transfer/info")
            if r.status_code == 200:
                return r.json()
            return {}
        except Exception as e:
            log.warning("获取传输信息异常: %s", e)
            return {}

    def get_stats(self) -> dict:
        return self.stats.as_dict()

    def is_healthy(self) -> bool:
        return self.stats.consecutive_failures < 3
