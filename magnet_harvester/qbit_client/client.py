"""qBittorrent WebAPI v2 客户端 v2.0"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Dict

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
        self._category_locks_guard = asyncio.Lock()
        self._sync_state = QBitSyncState()
        self._path_resolver = QBitPathResolver(
            get_categories=self.get_categories,
            get_torrents=self._get_torrents_list,
        )
        self._submitter = MagnetSubmitter(
            gateway=self,
            fs_base_path=self._config.fs_base_path,
            recorder=self.stats,
        )

    @property
    def last_error(self) -> str | None:
        return self.stats.last_error

    @last_error.setter
    def last_error(self, message: str | None) -> None:
        self.stats.last_error = message

    @property
    def _client(self):
        return self._transport._client

    async def replace_client(self) -> None:
        """关闭旧的 httpx client，下次请求时传输层会惰性重建。"""
        await self._transport.close()

    async def close(self):
        await self._transport.close()

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        return await self._transport.request(method, path, **kw)

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        """Submission-facing request operation."""
        return await self._req(method, path, **kw)

    async def ping(self) -> bool:
        now = time.monotonic()
        if self._last_ping_result is not None and now - self._last_ping_at < self._ping_cache_ttl:
            return self._last_ping_result
        async with self._ping_lock:
            # 双重检查：获取锁期间缓存可能已被另一个协程填充
            now = time.monotonic()
            if (
                self._last_ping_result is not None
                and now - self._last_ping_at < self._ping_cache_ttl
            ):
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
            raise RuntimeError(f"qB API get_maindata 异常: HTTP {r.status_code}")
        except httpx.TransportError as e:
            log.error(f"get_maindata 网络异常: {e}")
            raise
        except Exception as e:
            log.error(f"get_maindata 未知异常: {e}", exc_info=True)
            raise

    async def poll_torrent_snapshot(self) -> Dict[str, dict]:
        """增量同步 qB torrent 状态，并缓存当前快照。"""
        snapshot, _removed = await self._sync_state.poll(self.get_maindata)
        return snapshot

    async def poll_torrent_snapshot_with_removed(self) -> tuple[Dict[str, dict], set[str]]:
        """增量同步 qB torrent 状态，原子返回快照和本轮移除的哈希。"""
        return await self._sync_state.poll(self.get_maindata)

    def take_recently_removed(self) -> set[str]:
        return self._sync_state.take_recently_removed()

    @staticmethod
    def map_torrent_status(torrent: dict) -> dict:
        return TorrentStatusMapper.map(torrent)

    async def get_categories(self) -> dict:
        r = await self._req("GET", "/torrents/categories")
        if r.status_code != 200:
            raise RuntimeError(f"qB categories 查询失败: HTTP {r.status_code}")
        return r.json()

    async def find_torrent_by_prefix(self, hash_prefix: str) -> dict | None:
        """在 qB 种子列表中查找 hash 前缀匹配的种子（去重检测）。"""
        r = await self._req("GET", "/torrents/info")
        if r.status_code != 200:
            raise RuntimeError(f"qB torrent 查询失败: HTTP {r.status_code}")
        torrents = r.json()
        prefix_lower = hash_prefix.lower()
        for torrent in torrents:
            if torrent.get("hash", "").lower().startswith(prefix_lower):
                return torrent
        return None

    async def _get_torrents_list(self) -> list:
        """获取种子列表；失败时传播异常，成功空结果返回 []。"""
        r = await self._req("GET", "/torrents/info")
        if r.status_code != 200:
            raise RuntimeError(f"qB torrents 列表查询失败: HTTP {r.status_code}")
        return r.json()

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
        async with self._category_locks_guard:
            if name not in self._category_locks:
                if len(self._category_locks) >= self.MAX_CATEGORY_LOCKS:
                    # 从旧到新遍历，弹出第一个未被持有的锁
                    evicted = False
                    for key, lock in self._category_locks.items():
                        if not lock.locked():
                            del self._category_locks[key]
                            evicted = True
                            log.debug("LRU evicted category lock [%s]", key)
                            break
                    if not evicted:
                        log.error(
                            "分类锁 LRU 已达上限 (%d) 且全部被持有，拒绝创建 [%s]",
                            self.MAX_CATEGORY_LOCKS,
                            name,
                        )
                        return False
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

                    cat_entry = cats.get(name, {})
                    if not isinstance(cat_entry, dict):
                        log.warning(
                            "分类 [%s] 返回非 dict 类型: %s，跳过路径比对",
                            name,
                            type(cat_entry).__name__,
                        )
                        return False
                    if name in cats and cat_entry.get("savePath", "") != save_path:
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
        cats: dict = {}
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
        return await self._submitter.add_magnet(magnet, category, save_path)

    def get_stats(self) -> dict:
        return self.stats.as_dict()

    def is_healthy(self) -> bool:
        return self.stats.consecutive_failures < 3
