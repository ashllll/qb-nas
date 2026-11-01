"""Internal magnet submission helper for QBittorrentClient.

Extracted so the add-magnet / category-add behaviour can be tested and
reasoned about independently of connection state and transport concerns.
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
from pathlib import Path
from typing import Protocol

import httpx

from magnet_harvester.magnet_parser import HASH_RE
from magnet_harvester.utils.infohash import normalize_infohash
from magnet_harvester.qbit_client.paths import _safe_fs_segment

log = logging.getLogger(__name__)


class MagnetSubmissionGateway(Protocol):
    """qBittorrent operations needed by MagnetSubmitter."""

    async def request(self, method: str, path: str, **kw) -> httpx.Response: ...
    async def ensure_category(self, name: str, save_path: str) -> bool: ...
    async def get_base_save_path(self) -> str: ...
    async def find_torrent_by_prefix(self, hash_prefix: str) -> dict | None: ...


class SubmissionRecorder(Protocol):
    """Records submission metrics and last-error state."""

    def attempted(self) -> None: ...
    def succeeded(self) -> None: ...
    def failed(self) -> None: ...
    def error(self, message: str | None) -> None: ...


class NullSubmissionRecorder:
    def attempted(self) -> None:
        pass

    def succeeded(self) -> None:
        pass

    def failed(self) -> None:
        pass

    def error(self, message: str | None) -> None:
        pass


def _resolve_relative_save_path(base: str, relative_path: str) -> str:
    base_path = posixpath.normpath(base)
    candidate = posixpath.normpath(posixpath.join(base_path, relative_path))
    base_prefix = base_path.rstrip("/") + "/"
    if candidate != base_path and not candidate.startswith(base_prefix):
        raise ValueError("保存路径不能逃逸基础下载目录")
    return candidate


def _add_response_succeeded(response) -> bool:
    """qB v4 返回纯文本 "Ok."，v5 返回 JSON（success_count/failure_count/added_torrent_ids）。"""
    text = response.text.strip()
    if text.lower() == "ok.":
        return True
    if text.lower() == "fails.":
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if "failure_count" in payload:
        failures = int(payload.get("failure_count") or 0)
        added = payload.get("added_torrent_ids") or []
        success = int(payload.get("success_count") or 0)
        return failures == 0 and (success > 0 or bool(added))
    return False


class MagnetSubmitter:
    """Encapsulates adding a single magnet link to qBittorrent.

    All I/O sits behind ``MagnetSubmissionGateway`` and all metric/error
    state mutation sits behind ``SubmissionRecorder``. Callers learn two
    collaborators instead of the order and meaning of many callbacks.
    """

    def __init__(
        self,
        *,
        gateway: MagnetSubmissionGateway,
        fs_base_path: str = "",
        auto_create_dirs: bool = True,
        recorder: SubmissionRecorder | None = None,
    ) -> None:
        self._gateway = gateway
        self._fs_base_path = fs_base_path.strip()
        self._auto_create_dirs = auto_create_dirs
        self._recorder = recorder or NullSubmissionRecorder()

    async def add_magnet(self, magnet: str, category: str, save_path: str = "") -> bool:
        """Submit ``magnet`` to qBittorrent under ``category``.

        Behaviour matches the original ``QBittorrentClient.add_magnet``:

        - Validate the magnet link.
        - Resolve a category save path (absolute or relative to base path).
        - Create the local FS directory when ``fs_base_path`` is configured.
        - Ensure the category exists in qB.
        - Add the torrent with ``autoTMM=true`` and no per-torrent save path.
        - Treat "already exists" responses as success.
        """
        self._recorder.attempted()

        btih_match = HASH_RE.search(magnet)
        if not btih_match:
            self._recorder.error("磁力链接格式无效（缺少 btih）")
            self._recorder.failed()
            return False
        # 磁力里的 btih 可能是 base32 写法；qB 的种子列表一律是 40 位 hex，
        # 前缀比对必须用归一化后的 hex，否则「已存在」会被判成失败、条目误标 error。
        btih_raw = btih_match.group(1)
        btih_full = normalize_infohash(btih_raw) or btih_raw.upper()
        btih_prefix = btih_full[:8]

        category_save_path = save_path
        if category_save_path and not category_save_path.startswith("/"):
            base = await self._gateway.get_base_save_path()
            if base:
                try:
                    category_save_path = _resolve_relative_save_path(base, category_save_path)
                except ValueError as exc:
                    self._recorder.error(str(exc))
                    self._recorder.failed()
                    return False
        if not category_save_path:
            category_save_path = await self._gateway.get_base_save_path() or ""

        if not category_save_path:
            log.warning("无法解析分类保存路径，使用 qB 默认路径")

        if self._fs_base_path and self._auto_create_dirs:
            dir_path = str(Path(self._fs_base_path) / _safe_fs_segment(category))
            await asyncio.to_thread(os.makedirs, dir_path, exist_ok=True)

        try:
            category_ok = await self._gateway.ensure_category(category, category_save_path or "")
            if not category_ok:
                log.warning(f"分类 [{category}] 创建失败")
                self._recorder.error(f"分类 [{category}] 创建失败")
                self._recorder.failed()
                return False

            r = await self._gateway.request(
                "POST",
                "/torrents/add",
                data={
                    "urls": magnet,
                    "category": category,
                    "autoTMM": "true",
                    # 页面点「下载」就是要下：加入 qB 后立即开始，
                    # 不再用 paused/stopped 参数把种子停在已停止状态。
                },
            )

            ok = _add_response_succeeded(r)

            if ok:
                self._recorder.succeeded()
                log.debug(f"添加种子成功: {category}")
            else:
                existing = await self._gateway.find_torrent_by_prefix(btih_prefix)
                if existing:
                    log.info(
                        f"种子已存在于 qB (btih:{btih_prefix}…)，跳过: "
                        f"{existing.get('name', '?')[:40]}"
                    )
                    self._recorder.succeeded()
                    return True

                error_msg = f"qB 拒绝 (btih:{btih_prefix}…) — {r.text.strip()[:100]}"
                self._recorder.error(error_msg)
                self._recorder.failed()
                log.warning(f"add_magnet 失败: {error_msg}")

            return ok

        except Exception as e:
            self._recorder.error(str(e))
            self._recorder.failed()
            log.error(f"add_magnet 异常: {e}")
            return False
