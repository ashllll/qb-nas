"""qBittorrent WebAPI v2 客户端 v2.0"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Dict, List, Optional

import httpx

from magnet_harvester.config import QBitConfig, settings
from magnet_harvester.models import TaskStatus
from magnet_harvester.qbit_client.mapper import TorrentStatusMapper
from magnet_harvester.qbit_client.paths import QBitPathResolver, _safe_fs_segment
from magnet_harvester.qbit_client.stats import QBittorrentStats

log = logging.getLogger(__name__)

QBitApiObject = dict[str, object]


class QBittorrentClient:
    def __init__(self, config: QBitConfig = None):
        if config is None:
            self._config = QBitConfig(
                host=settings.QBIT_HOST,
                username=settings.QBIT_USERNAME,
                password=settings.QBIT_PASSWORD,
            )
        else:
            self._config = config
        self.host     = self._config.host.rstrip("/")
        self.username = self._config.username
        self.password = self._config.password
        self._cookie  = None
        self._client: Optional[httpx.AsyncClient] = None
        self.stats = QBittorrentStats()
        self._retry_config = {
            "max_retries": 3,
            "base_delay": 1.0,
            "max_delay": 10.0,
            "retry_on": [408, 429, 500, 502, 503, 504],
        }
        self._cached_default_path: str | None = None
        self.last_error: str | None = None
        self._maindata_rid = 0
        self._torrent_snapshot: Dict[str, dict] = {}
        self._recently_removed: set[str] = set()
        self._path_resolver = QBitPathResolver(
            get_categories=self.get_categories,
            get_torrents=self._get_torrents_list,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
            self._client = httpx.AsyncClient(
                limits=limits,
                timeout=httpx.Timeout(connect=10, read=30, write=30, pool=30),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._cookie = None

    async def _login(self, force: bool = False) -> bool:
        if not force and self._cookie:
            return True
        
        try:
            client = await self._get_client()
            r = await client.post(
                f"{self.host}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
            )
            
            if r.text.strip() == "Ok.":
                self._cookie = r.cookies
                log.info("qBittorrent 登录成功")
                return True
            
            log.error(f"qBittorrent 登录失败: {r.text[:100]}")
            self.stats.consecutive_failures += 1
            self.stats.last_failure_time = time.time()
            return False
            
        except Exception as e:
            log.error(f"qBittorrent 登录异常: {e}")
            self.stats.consecutive_failures += 1
            self.stats.last_failure_time = time.time()
            return False

    async def _req_with_retry(self, method: str, path: str, **kw) -> httpx.Response:
        config = self._retry_config
        last_exception = None
        auth_retry_count = 0
        max_auth_retries = 2
        
        for attempt in range(config["max_retries"]):
            try:
                if not self._cookie:
                    ok = await self._login()
                    if not ok:
                        raise RuntimeError("qBittorrent 登录失败")

                client = await self._get_client()
                
                cookies = self._cookie if self._cookie else None
                
                r = await client.request(
                    method,
                    f"{self.host}/api/v2{path}",
                    cookies=cookies,
                    **kw
                )

                if r.status_code == 403:
                    if auth_retry_count >= max_auth_retries:
                        raise RuntimeError(f"qBittorrent Session 过期（已重试{max_auth_retries}次）")
                    
                    log.warning(f"qBittorrent Session 过期，重新登录...")
                    self._cookie = None
                    auth_retry_count += 1
                    ok = await self._login(force=True)
                    if not ok:
                        raise RuntimeError("qBittorrent 重新登录失败")
                    continue

                if r.status_code in config["retry_on"] and attempt < config["max_retries"] - 1:
                    delay = min(config["base_delay"] * (2 ** attempt), config["max_delay"])
                    log.warning(f"qBittorrent 请求失败 ({r.status_code})，{delay:.1f}秒后重试...")
                    await asyncio.sleep(delay)
                    continue

                return r

            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < config["max_retries"] - 1:
                    delay = min(config["base_delay"] * (2 ** attempt), config["max_delay"])
                    log.warning(f"qBittorrent 请求超时，{delay:.1f}秒后重试...")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"qBittorrent 请求超时（已重试{config['max_retries']}次）")
                    
            except httpx.ConnectError as e:
                last_exception = e
                if attempt < config["max_retries"] - 1:
                    delay = min(config["base_delay"] * (2 ** attempt), config["max_delay"])
                    log.warning(f"qBittorrent 连接失败，{delay:.1f}秒后重试...")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"qBittorrent 连接失败（已重试{config['max_retries']}次）")

            except Exception as e:
                last_exception = e
                log.error(f"qBittorrent 请求异常: {e}")
                break

        raise last_exception or RuntimeError("qBittorrent 请求失败")

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        return await self._req_with_retry(method, path, **kw)

    async def ping(self) -> bool:
        try:
            r = await self._req("GET", "/app/version")
            return r.status_code == 200
        except Exception as e:
            log.warning(f"qBittorrent ping 失败: {e}")
            return False

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

        # 1. 根据 qB 默认路径生成分类的 savePath（用于 createCategory，不用于 add）
        if save_path and not save_path.startswith("/"):
            base = await self.get_base_save_path()
            if base:
                save_path = f"{base}/{save_path}"
            else:
                save_path = ""

        # 2. 如果配置了 FS_BASE_PATH，先创建真实目录（qB 的 createCategory 不是 mkdir）
        fs_base = settings.FS_BASE_PATH.strip()
        if fs_base:
            (Path(fs_base) / _safe_fs_segment(category)).mkdir(parents=True, exist_ok=True)

        try:
            # 2. 确保分类存在且路径正确
            if save_path:
                category_ok = await self.ensure_category(category, save_path)
                if not category_ok:
                    log.warning(f"分类 [{category}] 创建失败")

            # 3. 添加任务 — 只传 category + autoTMM，不传 savepath
            r = await self._req("POST", "/torrents/add", data={
                "urls":     magnet,
                "category": category,
                "autoTMM":  "true",
            })

            ok = r.text.strip() == "Ok."

            if ok:
                self.stats.total_success += 1
                self.stats.consecutive_failures = 0
                self.stats.last_success_time = time.time()
                log.debug(f"添加种子成功: {category}")
            else:
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
