"""通过 aria2bridge 把磁力任务送入 fnOS 下载中心。"""

from __future__ import annotations

import asyncio
import posixpath
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

import httpx

from magnet_harvester.models import TaskStatus
from magnet_harvester.qbit_client.paths import _safe_fs_segment
from magnet_harvester.qbit_client.stats import QBittorrentStats
from magnet_harvester.qbit_client.submitter import _resolve_relative_save_path
from magnet_harvester.utils.infohash import normalize_infohash
from magnet_harvester.utils.bg_tasks import BGTaskManager

if TYPE_CHECKING:
    from magnet_harvester.config import Aria2Config


class Aria2RPCError(RuntimeError):
    """服务器明确返回的 JSON-RPC 错误，与网络中断区分。"""


class Aria2NotSentError(RuntimeError):
    """连接尚未建立或连接池超时，RPC 请求确定没有发出。"""


class Aria2BridgeClient:
    submission_timeout = 120.0

    def __init__(self, config: Aria2Config) -> None:
        self.config = config
        self.stats = QBittorrentStats()
        self._client: httpx.AsyncClient | None = None
        self._db: sqlite3.Connection | None = None
        self._db_lock = asyncio.Lock()
        self._submit_lock = asyncio.Lock()

    @property
    def last_error(self) -> str | None:
        return self.stats.last_error

    def _safe_error(self, error: object) -> str:
        message = str(error)
        if self.config.secret:
            message = message.replace(self.config.secret, "[已隐藏]")
        return message[:500]

    async def _query(self, sql: str, params: tuple = ()) -> list[tuple]:
        def execute() -> list[tuple]:
            if self._db is None:
                if self.config.state_db_path != ":memory:":
                    Path(self.config.state_db_path).parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(
                    self.config.state_db_path, timeout=30, check_same_thread=False
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS aria2_bindings ("
                    "endpoint TEXT NOT NULL, hash TEXT NOT NULL, gid TEXT, "
                    "complete INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(endpoint, hash))"
                )
            with self._db:
                return self._db.execute(sql, params).fetchall()

        async with self._db_lock:
            task = BGTaskManager.spawn(asyncio.to_thread(execute), name="aria2-binding-write")
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # SQLite 线程不会随协程取消；等它提交后再释放锁、关闭连接。
                await task
                raise

    async def _rpc(self, method: str, params: list | None = None) -> object:
        self.config.validate()
        if self._client is None:
            # 桥接会等待 fnOS 响应并核对超时任务，不能用页面探针的短超时。
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))
        request_id = uuid4().hex
        try:
            response = await self._client.post(
                self.config.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": [f"token:{self.config.secret}", *(params or [])],
                },
            )
            response.raise_for_status()
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or payload.get("jsonrpc") != "2.0"
                or payload.get("id") != request_id
            ):
                raise ValueError("无效的 JSON-RPC 响应")
            if "error" in payload:
                error = payload["error"]
                message = error.get("message", "RPC 请求失败") if isinstance(error, dict) else error
                raise Aria2RPCError(self._safe_error(message))
            if "result" not in payload:
                raise ValueError("JSON-RPC 响应缺少 result")
            return payload["result"]
        except (httpx.HTTPError, ValueError) as exc:
            error_type = (
                Aria2NotSentError
                if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
                else RuntimeError
            )
            raise error_type(
                f"fnOS 桥接请求失败（{type(exc).__name__}）：{self._safe_error(exc)}"
            ) from None

    async def ping(self) -> bool:
        try:
            version = await self._rpc("aria2.getVersion")
            if not isinstance(version, dict) or "bridged-to-fnos-downloadcenter" not in version.get(
                "enabledFeatures", []
            ):
                raise RuntimeError("目标不是 fnOS 下载中心 Aria2 桥接")
            status = await self._rpc("aria2.getGlobalStat")
            if not isinstance(status, dict) or "numActive" not in status:
                raise RuntimeError("fnOS 下载中心状态响应无效")
        except asyncio.CancelledError:
            self.stats.error("fnOS 下载中心探测超时或已取消")
            raise
        except Exception as exc:
            self.stats.error(self._safe_error(exc))
            return False
        self.stats.error(None)
        return True

    async def _save_dir(self, category: str, save_path: str) -> str:
        base = self.config.save_path
        if not base:
            options = await self._rpc("aria2.getGlobalOption")
            base = options.get("dir", "") if isinstance(options, dict) else ""
        relative = save_path or _safe_fs_segment(category)
        for path in (base, relative):
            if (
                not isinstance(path, str)
                or "\\" in path
                or any(ord(char) < 32 for char in path)
                or any(part in {".", ".."} for part in path.split("/"))
            ):
                raise ValueError("保存路径不能包含上级目录、反斜线或空字符")
        if not re.match(r"^/vol[1-9]\d*/", base):
            raise ValueError("桥接默认保存目录必须是 NAS 的 /volN/ 路径")
        if relative.startswith("/"):
            if not re.match(r"^/vol[1-9]\d*/", relative):
                raise ValueError("保存目录必须是 NAS 的 /volN/ 路径，不能使用 Docker 内部路径")
            return posixpath.normpath(relative)
        return _resolve_relative_save_path(base, relative)

    async def add_magnet(self, magnet: str, category: str, save_path: str = "") -> bool:
        self.stats.attempted()
        async with self._submit_lock:
            try:
                self.config.validate()
                hashes = {
                    normalize_infohash(xt[9:])
                    for xt in parse_qs(urlsplit(magnet).query).get("xt", [])
                    if xt.lower().startswith("urn:btih:")
                }
                if not magnet.lower().startswith("magnet:?") or len(hashes) != 1 or None in hashes:
                    raise ValueError("磁力链接格式无效（需要唯一完整 btih）")
                hash_key = hashes.pop().lower()
                identity = (self.config.rpc_url, hash_key)
                rows = await self._query(
                    "SELECT gid, complete FROM aria2_bindings WHERE endpoint=? AND hash=?", identity
                )
                if rows:
                    gid, terminal = rows[0]
                    if not gid:
                        raise RuntimeError(
                            "上次提交结果未知，请先在 NAS 下载中心核对，不能自动重复提交"
                        )
                    task = {"gid": gid, "status": "removed"}
                    if terminal != 2:
                        try:
                            task = await self._rpc("aria2.tellStatus", [gid])
                        except Aria2RPCError as exc:
                            if str(exc) != f"GID#{gid} is not found":
                                raise
                    if not isinstance(task, dict) or task.get("gid") != gid:
                        raise RuntimeError("fnOS 返回的任务身份不匹配")
                    if task.get("status") == "error":
                        raise RuntimeError("任务已存在，请在 NAS 下载中心处理错误或恢复任务")
                    if task.get("status") == "removed":
                        await self._query(
                            "DELETE FROM aria2_bindings WHERE endpoint=? AND hash=?", identity
                        )
                        rows = []
                if not rows:
                    directory = await self._save_dir(category, save_path)
                    # 先记录未决提交；断电、取消或超时后不能把同一磁力盲目再送一次。
                    await self._query(
                        "INSERT INTO aria2_bindings(endpoint, hash) VALUES (?, ?)", identity
                    )
                    try:
                        gid = await self._rpc("aria2.addUri", [[magnet], {"dir": directory}])
                    except (Aria2RPCError, Aria2NotSentError) as exc:
                        # 只放行明确未发送或在调用下载中心之前被拒绝的请求。
                        if isinstance(exc, Aria2NotSentError) or str(exc) == "Token 验证失败":
                            await self._query(
                                "DELETE FROM aria2_bindings WHERE endpoint=? AND hash=?", identity
                            )
                        raise
                    if not isinstance(gid, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", gid):
                        raise RuntimeError("桥接没有返回有效任务 GID，请在 NAS 下载中心核对")
                    await self._query(
                        "UPDATE aria2_bindings SET gid=? WHERE endpoint=? AND hash=?",
                        (gid, *identity),
                    )
                self.stats.succeeded()
                self.stats.error(None)
                return True
            except Exception as exc:
                self.stats.error(self._safe_error(exc))
                self.stats.failed()
                return False

    async def poll_torrent_snapshot_with_removed(self) -> tuple[dict, set[str]]:
        if not self.config.rpc_url or not self.config.secret:
            return {}, set()
        self.config.validate()
        rows = await self._query(
            "SELECT hash, gid, complete FROM aria2_bindings WHERE endpoint=? AND gid IS NOT NULL",
            (self.config.rpc_url,),
        )
        snapshot: dict = {}
        removed: set[str] = set()
        completed: list[tuple[str, str]] = []
        tasks: dict[str, dict] = {}
        if any(terminal == 0 for _, _, terminal in rows):
            # 每个桥接查询都会遍历整个 NAS 任务列表，按状态批查避免 N 次全表查询。
            for method, params in (
                ("aria2.tellActive", []),
                ("aria2.tellWaiting", [0, 100000]),
                ("aria2.tellStopped", [0, 100000]),
            ):
                result = await self._rpc(method, params)
                if not isinstance(result, list):
                    raise RuntimeError("fnOS 返回的任务列表无效")
                for task in result:
                    if not isinstance(task, dict) or not isinstance(task.get("gid"), str):
                        raise RuntimeError("fnOS 返回的任务列表含无效任务身份")
                    tasks[task["gid"]] = task
        for hash_key, gid, terminal in rows:
            if terminal == 2:
                removed.add(hash_key)
                continue
            try:
                task = (
                    {"gid": gid, "status": "complete"}
                    if terminal == 1
                    else tasks.get(gid) or await self._rpc("aria2.tellStatus", [gid])
                )
            except Aria2RPCError as exc:
                if str(exc) != f"GID#{gid} is not found":
                    raise
                removed.add(hash_key)
                continue
            if not isinstance(task, dict) or task.get("gid") != gid:
                raise RuntimeError("fnOS 返回的任务身份不匹配")
            state = task.get("status")
            if state == "removed":
                removed.add(hash_key)
                continue
            statuses = {
                "active": TaskStatus.downloading,
                "waiting": TaskStatus.queued,
                "paused": TaskStatus.paused,
                "complete": TaskStatus.success,
                "error": TaskStatus.error,
            }
            if state not in statuses:
                raise RuntimeError("fnOS 返回未知任务状态")
            total = max(0, int(task.get("totalLength") or 0))
            done = max(0, int(task.get("completedLength") or 0))
            progress = 100 if state == "complete" else min(100, done / total * 100) if total else 0
            snapshot[hash_key] = {
                "download_state": {
                    "status": statuses[state],
                    "progress": round(progress, 1),
                    "torrent_state": f"aria2:{state}",
                    "error_msg": self._safe_error(task.get("errorMessage") or "下载任务出错")
                    if state == "error"
                    else None,
                }
            }
            if state == "complete":
                completed.append((hash_key, gid))
        for hash_key, gid, _ in rows:
            if hash_key in removed:
                # 轮询期间用户可能重新提交，旧 GID 的结果不能写入或传给状态机。
                matched = await self._query(
                    "UPDATE aria2_bindings SET complete=2 WHERE endpoint=? AND hash=? AND gid=? "
                    "RETURNING hash",
                    (self.config.rpc_url, hash_key, gid),
                )
                if not matched:
                    removed.discard(hash_key)
        for hash_key, gid in completed:
            matched = await self._query(
                "UPDATE aria2_bindings SET complete=1 WHERE endpoint=? AND hash=? AND gid=? "
                "RETURNING hash",
                (self.config.rpc_url, hash_key, gid),
            )
            if not matched:
                snapshot.pop(hash_key, None)
        return snapshot, removed

    def get_stats(self) -> dict:
        return self.stats.as_dict()

    def is_healthy(self) -> bool:
        return self.stats.consecutive_failures < 3

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        async with self._db_lock:
            if self._db is not None:
                await asyncio.to_thread(self._db.close)
                self._db = None
