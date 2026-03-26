"""qBittorrent WebAPI v2 客户端"""
from __future__ import annotations

import logging

import httpx

from config import settings

log = logging.getLogger(__name__)


class QBittorrentClient:
    def __init__(self):
        self.host     = settings.QBIT_HOST.rstrip("/")
        self.username = settings.QBIT_USERNAME
        self.password = settings.QBIT_PASSWORD
        self._cookie  = None

    async def _login(self) -> bool:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{self.host}/api/v2/auth/login",
                    data={"username": self.username, "password": self.password},
                    timeout=10,
                )
                if r.text.strip() == "Ok.":
                    self._cookie = r.cookies
                    log.debug("qBittorrent 登录成功")
                    return True
                log.error(f"qBittorrent 登录失败: {r.text[:100]}")
                return False
        except Exception as e:
            log.error(f"qBittorrent 登录异常: {e}")
            return False

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        if not self._cookie:
            ok = await self._login()
            if not ok:
                raise RuntimeError("qBittorrent 登录失败，无法发送请求")

        async with httpx.AsyncClient(cookies=self._cookie) as c:
            r = await c.request(method, f"{self.host}/api/v2{path}", timeout=15, **kw)

        if r.status_code == 403:
            ok = await self._login()
            if not ok:
                raise RuntimeError("qBittorrent 重新登录失败")
            async with httpx.AsyncClient(cookies=self._cookie) as c2:
                r = await c2.request(method, f"{self.host}/api/v2{path}", timeout=15, **kw)

        return r

    async def ping(self) -> bool:
        try:
            r = await self._req("GET", "/app/version")
            return r.status_code == 200
        except Exception:
            return False

    async def get_categories(self) -> dict:
        # 只捕获 JSON 解析错误，登录失败的 RuntimeError 向上传播
        r = await self._req("GET", "/torrents/categories")
        if r.status_code != 200:
            log.warning(f"get_categories 返回 {r.status_code}")
            return {}
        try:
            return r.json()
        except Exception:
            log.warning(f"get_categories 响应非 JSON: {r.text[:80]}")
            return {}

    async def ensure_category(self, name: str, save_path: str):
        cats = await self.get_categories()
        if name not in cats:
            await self._req("POST", "/torrents/createCategory",
                            data={"category": name, "savePath": save_path})
            log.info(f"创建分类: [{name}] → {save_path}")
        elif cats[name].get("savePath", "") != save_path:
            await self._req("POST", "/torrents/editCategory",
                            data={"category": name, "savePath": save_path})
            log.info(f"更新分类路径: [{name}] → {save_path}")

    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool:
        await self.ensure_category(category, save_path)
        r = await self._req("POST", "/torrents/add", data={
            "urls":     magnet,
            "category": category,
            "savepath": save_path,
            "autoTMM":  "false",
        })
        ok = r.text.strip() == "Ok."
        if not ok:
            log.warning(f"add_magnet 失败: {r.text[:100]}")
        return ok

    async def get_torrents(self) -> list:
        try:
            r = await self._req("GET", "/torrents/info")
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []


qbit = QBittorrentClient()
