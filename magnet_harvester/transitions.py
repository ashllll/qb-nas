"""
MagnetItemTransitions — applies Magnet item state changes and publishes events.

This module encapsulates the knowledge of what state transitions exist for a
Magnet item and which events each transition should publish.

Used by HarvestPipeline during crawl→classify→download orchestration.
"""

from __future__ import annotations

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.qbit_client.mapper import TorrentStatusMapper
from magnet_harvester.utils.serializers import item_payload
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import ItemStore


class MagnetItemTransitions:
    """Applies Magnet item state changes and publishes matching events.

    Each method performs a store update + event emission as an atomic
    conceptual transition.
    """

    def __init__(self, store: ItemStore, bus: MessageBus):
        self._store = store
        self._bus = bus

    # ── Event emission helpers (inlined from ItemEventEmitter) ──

    async def _emit_item_changed(self, hash_key: str) -> None:
        item = self._store.get(hash_key)
        if item is not None:
            await self._bus.emit(Event(EventType.STORE_CHANGED, {"item": item_payload(item)}))

    async def _emit_download_result(
        self, hash_key: str, previous_status: TaskStatus | None = None
    ) -> None:
        item = self._store.get(hash_key)
        if item is None:
            return
        is_terminal = item.status in {TaskStatus.success, TaskStatus.error}
        is_new_phase = previous_status in {
            TaskStatus.pending,
            TaskStatus.adding,
            TaskStatus.classifying,
            None,
        }
        if is_terminal or is_new_phase:
            await self._bus.emit(
                Event(
                    EventType.DOWNLOAD_RESULT,
                    {
                        "hash": hash_key,
                        "status": item.status.value,
                        "error_msg": item.error_msg,
                        "progress": item.progress,
                        "torrent_state": item.torrent_state,
                    },
                )
            )

    # ── State transitions ──

    async def found(self, item: MagnetItem) -> bool:
        if not self._store.add(item):
            return False
        await self._bus.emit(Event(EventType.MAGNET_FOUND, {"item": item.model_dump()}))
        return True

    async def classification_started(self, hash_key: str):
        if not self._store.update(hash_key, status=TaskStatus.classifying, error_msg=None):
            return
        await self._emit_item_changed(hash_key)

    async def classified(self, hash_key: str, result: dict):
        if not self._store.update(
            hash_key,
            category=result["category"],
            save_path=result["save_path"],
            status=TaskStatus.pending,
            progress=0.0,
            torrent_state=None,
            error_msg=None,
        ):
            return
        await self._bus.emit(
            Event(
                EventType.CLASSIFY_DONE,
                {
                    "hash": hash_key,
                    "category": result["category"],
                    "confidence": result.get("confidence", ""),
                    "reason": result.get("reason", ""),
                },
            )
        )
        await self._emit_item_changed(hash_key)

    async def download_submitting(self, hash_key: str):
        item = self._store.get(hash_key)
        if item is None:
            return
        if not self._store.update(
            hash_key,
            status=TaskStatus.adding,
            progress=0.0,
            torrent_state="submitting",
            error_msg=None,
        ):
            return
        await self._emit_item_changed(hash_key)
        await self._bus.emit(Event(EventType.DOWNLOAD_START, {"hash": hash_key, "name": item.name}))

    async def download_submitted(self, hash_key: str):
        if not self._store.update(
            hash_key,
            status=TaskStatus.queued,
            torrent_state="submitted",
            progress=0.0,
            error_msg=None,
        ):
            return
        await self._emit_item_changed(hash_key)
        await self._emit_download_result(hash_key, previous_status=TaskStatus.adding)

    async def download_failed(self, hash_key: str, error_msg: str):
        if not self._store.update(hash_key, status=TaskStatus.error, error_msg=error_msg):
            return
        await self._emit_item_changed(hash_key)
        await self._emit_download_result(hash_key, previous_status=TaskStatus.adding)

    # ── 以下方法来自 item_transitions.py（移植版）──

    async def clipboard_found(self, item: MagnetItem) -> bool:
        """found + emit_item_changed（剪贴板入口专用）"""
        if not await self.found(item):
            return False
        await self._emit_item_changed(item.hash)
        return True

    async def manually_classified(self, hash_key: str, category: str) -> bool:
        """手动分类：更新 + CLASSIFY_DONE + emit_item_changed"""
        if not self._store.update(hash_key, category=category, save_path=""):
            return False
        await self._bus.emit(
            Event(
                EventType.CLASSIFY_DONE,
                {
                    "hash": hash_key,
                    "category": category,
                    "confidence": "manual",
                    "reason": "手动修改",
                },
            )
        )
        await self._emit_item_changed(hash_key)
        return True

    async def download_removed(self, hash_key: str, previous_status: TaskStatus | None):
        """种子已从 qBittorrent 中消失"""
        if not self._store.update(
            hash_key,
            status=TaskStatus.error,
            error_msg="种子已从 qBittorrent 中消失",
            torrent_state="removed",
        ):
            return
        await self.download_state_changed(hash_key, previous_status)

    async def download_status_changed(
        self,
        hash_key: str,
        *,
        fields: dict,
        previous_status: TaskStatus | None,
    ):
        """同步 qB 状态：更新字段 + download_state_changed"""
        if not fields:
            return
        if not self._store.update(hash_key, **fields):
            return
        await self.download_state_changed(hash_key, previous_status)

    async def download_state_changed(
        self,
        hash_key: str,
        previous_status: TaskStatus | None = None,
    ):
        """emit_item_changed + 有条件 emit_download_result"""
        await self._emit_item_changed(hash_key)
        await self._emit_download_result(hash_key, previous_status)

    async def reconcile_download_snapshot(
        self,
        hash_key: str,
        item: MagnetItem,
        torrent: dict | None,
        *,
        was_removed: bool = False,
    ) -> bool:
        """Reconcile a tracked MagnetItem against a qBittorrent torrent snapshot.

        If the torrent snapshot is missing and the hash was recently removed,
        mark the item as error/removed (unless it already succeeded). If a
        torrent snapshot is present, compute field diffs via *mapper* and apply
        a status-changed transition.

        Returns True if the item was modified, False otherwise.
        """
        if torrent is None:
            if was_removed and item.status != TaskStatus.success:
                await self.download_removed(hash_key, item.status)
                return True
            return False

        mapped = TorrentStatusMapper.map(torrent)
        fields: dict = {}

        if item.status != mapped["status"]:
            fields["status"] = mapped["status"]
        if item.progress != mapped["progress"]:
            fields["progress"] = mapped["progress"]
        if item.torrent_state != mapped["torrent_state"]:
            fields["torrent_state"] = mapped["torrent_state"]
        if item.error_msg and mapped["status"] != TaskStatus.error:
            fields["error_msg"] = None

        if fields:
            await self.download_status_changed(
                hash_key,
                fields=fields,
                previous_status=item.status,
            )
            return True
        return False

    async def cleared(self) -> int:
        """清空全部 + ITEMS_CLEARED"""
        count = self._store.count
        self._store.clear()
        await self._bus.emit(Event(EventType.ITEMS_CLEARED, {"type": "items_cleared"}))
        return count
