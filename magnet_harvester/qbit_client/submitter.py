"""Internal magnet submission helper for QBittorrentClient.

Extracted so the add-magnet / category-add behaviour can be tested and
reasoned about independently of connection state and transport concerns.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import httpx

from magnet_harvester.magnet_parser import HASH_RE
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
        recorder: SubmissionRecorder | None = None,
    ) -> None:
        self._gateway = gateway
        self._fs_base_path = fs_base_path.strip()
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
        btih_full = btih_match.group(1).upper()
        btih_prefix = btih_full[:8]

        category_save_path = save_path
        if category_save_path and not category_save_path.startswith("/"):
            base = await self._gateway.get_base_save_path()
            if base:
                category_save_path = f"{base}/{category_save_path}"
        if not category_save_path:
            category_save_path = save_path

        if self._fs_base_path:
            (Path(self._fs_base_path) / _safe_fs_segment(category)).mkdir(
                parents=True, exist_ok=True
            )

        try:
            if category_save_path:
                category_ok = await self._gateway.ensure_category(category, category_save_path)
                if not category_ok:
                    log.warning(f"分类 [{category}] 创建失败")

            r = await self._gateway.request(
                "POST",
                "/torrents/add",
                data={
                    "urls": magnet,
                    "category": category,
                    "autoTMM": "true",
                },
            )

            ok = r.text.strip() == "Ok."

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
