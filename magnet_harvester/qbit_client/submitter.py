"""Internal magnet submission helper for QBittorrentClient.

Extracted so the add-magnet / category-add behaviour can be tested and
reasoned about independently of connection state and transport concerns.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

import httpx

from magnet_harvester.qbit_client.paths import _safe_fs_segment

log = logging.getLogger(__name__)

RequestCallback = Callable[..., Awaitable[httpx.Response]]
EnsureCategoryCallback = Callable[[str, str], Awaitable[bool]]
GetBasePathCallback = Callable[[], Awaitable[str]]
FindTorrentCallback = Callable[[str], Awaitable[Optional[dict]]]
SetLastErrorCallback = Callable[[Optional[str]], None]
NoArgCallback = Callable[[], None]


class MagnetSubmitter:
    """Encapsulates adding a single magnet link to qBittorrent.

    All I/O and state mutations are injected as callbacks so callers
    (notably ``QBittorrentClient``) keep their existing public API while
    tests can drive the submitter in isolation.
    """

    def __init__(
        self,
        *,
        request: RequestCallback,
        ensure_category: EnsureCategoryCallback,
        get_base_save_path: GetBasePathCallback,
        find_torrent_by_prefix: FindTorrentCallback,
        fs_base_path: str = "",
        set_last_error: Optional[SetLastErrorCallback] = None,
        record_attempt: Optional[NoArgCallback] = None,
        record_success: Optional[NoArgCallback] = None,
        record_failure: Optional[NoArgCallback] = None,
    ) -> None:
        self._request = request
        self._ensure_category = ensure_category
        self._get_base_save_path = get_base_save_path
        self._find_torrent_by_prefix = find_torrent_by_prefix
        self._fs_base_path = fs_base_path.strip()
        self._set_last_error = set_last_error
        self._record_attempt = record_attempt
        self._record_success = record_success
        self._record_failure = record_failure

    def _set_error(self, message: Optional[str]) -> None:
        if self._set_last_error:
            self._set_last_error(message)

    def _mark_attempt(self) -> None:
        if self._record_attempt:
            self._record_attempt()

    def _mark_success(self) -> None:
        if self._record_success:
            self._record_success()

    def _mark_failure(self) -> None:
        if self._record_failure:
            self._record_failure()

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
        self._mark_attempt()

        btih_match = re.search(r'btih:([A-Za-z0-9]{8,40})', magnet)
        if not btih_match:
            self._set_error("磁力链接格式无效（缺少 btih）")
            self._mark_failure()
            return False
        btih_full = btih_match.group(1).upper()
        btih_prefix = btih_full[:8]

        category_save_path = save_path
        if category_save_path and not category_save_path.startswith("/"):
            base = await self._get_base_save_path()
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
                category_ok = await self._ensure_category(category, category_save_path)
                if not category_ok:
                    log.warning(f"分类 [{category}] 创建失败")

            r = await self._request("POST", "/torrents/add", data={
                "urls": magnet,
                "category": category,
                "use_auto_torrent_management": "true",
            })

            ok = r.text.strip() == "Ok."

            if ok:
                self._mark_success()
                log.debug(f"添加种子成功: {category}")
            else:
                existing = await self._find_torrent_by_prefix(btih_prefix)
                if existing:
                    log.info(
                        f"种子已存在于 qB (btih:{btih_prefix}…)，跳过: "
                        f"{existing.get('name', '?')[:40]}"
                    )
                    self._mark_success()
                    return True

                error_msg = f"qB 拒绝 (btih:{btih_prefix}…) — {r.text.strip()[:100]}"
                self._set_error(error_msg)
                self._mark_failure()
                log.warning(f"add_magnet 失败: {error_msg}")

            return ok

        except Exception as e:
            self._set_error(str(e))
            self._mark_failure()
            log.error(f"add_magnet 异常: {e}")
            return False
