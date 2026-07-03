"""
MagnetItemTransitions — applies Magnet item state changes and publishes events.

This module encapsulates the knowledge of what state transitions exist for a
Magnet item and which events each transition should publish.

Organized into 3 domain objects (Discovery, Classification, Download) with
MagnetItemTransitions as a facade that delegates to them. The facade preserves
the original public API so callers are unaffected.

Used by HarvestPipeline during crawl→classify→download orchestration.
"""

from __future__ import annotations

import logging

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.qbit_client.mapper import TorrentStatusMapper
from magnet_harvester.utils.serializers import item_payload
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import ItemStore

log = logging.getLogger(__name__)


# ── 共享事件发射辅助 ──────────────────────────


class _TransitionBase:
    """域对象基类：封装 store/bus 依赖和事件发射辅助方法。"""

    def __init__(self, store: ItemStore, bus: MessageBus):
        self._store = store
        self._bus = bus

    async def _emit_item_changed(self, hash_key: str) -> None:
        """发射 STORE_CHANGED 事件（如果 item 存在）。"""
        item = self._store.get(hash_key)
        if item is not None:
            await self._bus.emit(Event(EventType.STORE_CHANGED, {"item": item_payload(item)}))

    async def _emit_download_result(
        self, hash_key: str, previous_status: TaskStatus | None = None
    ) -> None:
        """发射 DOWNLOAD_RESULT 事件（状态变化或进入终态时）。"""
        item = self._store.get(hash_key)
        if item is None:
            return
        if previous_status is not None and previous_status == item.status:
            return  # 状态未变化，跳过重复事件
        is_terminal = item.status in {TaskStatus.success, TaskStatus.error}
        is_new_phase = previous_status in {
            TaskStatus.pending,
            TaskStatus.adding,
            TaskStatus.classifying,
            None,
        }
        if is_terminal or is_new_phase:
            # 在 emit 前再次获取最新状态，减少 TOCTOU 窗口
            item = self._store.get(hash_key)
            if item is None:
                return
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


# ── 域对象 ────────────────────────────────────


class DiscoveryTransitions(_TransitionBase):
    """发现域：磁力链接发现相关的状态转换。"""

    async def found(self, item: MagnetItem) -> bool:
        if not self._store.add(item):
            return False
        await self._bus.emit(Event(EventType.MAGNET_FOUND, {"item": item.model_dump()}))
        return True

    async def clipboard_found(self, item: MagnetItem) -> bool:
        """found + emit_item_changed（剪贴板入口专用）"""
        if not await self.found(item):
            return False
        await self._emit_item_changed(item.hash)
        return True


class ClassificationTransitions(_TransitionBase):
    """分类域：分类生命周期相关的状态转换。"""

    async def started(self, hash_key: str):
        item = self._store.get(hash_key)
        if item is not None and item.status == TaskStatus.classifying:
            return  # 已在分类中，拒绝重复调用
        if not self._store.update(hash_key, status=TaskStatus.classifying, error_msg=None):
            return
        await self._emit_item_changed(hash_key)

    async def classified(self, hash_key: str, result: dict):
        category = result.get("category", "其他")
        if not self._store.update(
            hash_key,
            category=category,
            save_path=result.get("save_path", ""),
            status=TaskStatus.pending,
            progress=0.0,
            torrent_state=None,
            error_msg=None,
        ):
            log.warning("classified update 失败 %s (条目可能已被并发删除)", hash_key)
            return
        await self._bus.emit(
            Event(
                EventType.CLASSIFY_DONE,
                {
                    "hash": hash_key,
                    "category": category,
                    "confidence": result.get("confidence", ""),
                    "reason": result.get("reason", ""),
                },
            )
        )
        await self._emit_item_changed(hash_key)

    async def failed(self, hash_key: str, error_msg: str):
        """分类失败时回退状态到 pending，以便后续重试。"""
        if not self._store.update(hash_key, status=TaskStatus.pending, error_msg=error_msg):
            return
        await self._emit_item_changed(hash_key)

    async def manually_classified(self, hash_key: str, category: str) -> bool:
        """手动分类：更新 + CLASSIFY_DONE + emit_item_changed"""
        item = self._store.get(hash_key)
        save_path = item.save_path if item and item.save_path else ""
        if not self._store.update(hash_key, category=category, save_path=save_path):
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


class DownloadTransitions(_TransitionBase):
    """下载域：下载生命周期相关的状态转换。"""

    async def submitting(self, hash_key: str):
        item = self._store.get(hash_key)
        if item is None:
            return
        # 前置状态检查：只允许从 pending 或 error 状态转换到 adding
        if item.status not in {TaskStatus.pending, TaskStatus.error}:
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

    async def submitted(self, hash_key: str):
        item = self._store.get(hash_key)
        previous_status = item.status if item else TaskStatus.adding
        if not self._store.update(
            hash_key,
            status=TaskStatus.queued,
            torrent_state="submitted",
            progress=0.0,
            error_msg=None,
        ):
            return
        await self._emit_item_changed(hash_key)
        await self._emit_download_result(hash_key, previous_status=previous_status)

    async def failed(self, hash_key: str, error_msg: str):
        item = self._store.get(hash_key)
        if item is None:
            return
        previous_status = item.status
        # 前置状态检查：只允许从非终态转换到 error，已成功的种子不能被错误标记
        if item.status in {TaskStatus.success, TaskStatus.error, TaskStatus.skipped}:
            return
        if not self._store.update(hash_key, status=TaskStatus.error, error_msg=error_msg):
            return
        await self._emit_item_changed(hash_key)
        await self._emit_download_result(hash_key, previous_status=previous_status)

    async def removed(self, hash_key: str, previous_status: TaskStatus | None):
        """种子已从 qBittorrent 中消失"""
        if not self._store.update(
            hash_key,
            status=TaskStatus.error,
            error_msg="种子已从 qBittorrent 中消失",
            torrent_state="removed",
        ):
            return
        await self.state_changed(hash_key, previous_status)

    async def status_changed(
        self,
        hash_key: str,
        *,
        fields: dict,
        previous_status: TaskStatus | None,
    ):
        """同步 qB 状态：更新字段 + state_changed"""
        if not fields:
            return
        if not self._store.update(hash_key, **fields):
            return
        await self.state_changed(hash_key, previous_status)

    async def state_changed(
        self,
        hash_key: str,
        previous_status: TaskStatus | None = None,
    ):
        """emit_item_changed + 有条件 emit_download_result"""
        await self._emit_item_changed(hash_key)
        await self._emit_download_result(hash_key, previous_status)

    async def reconcile_snapshot(
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
                await self.removed(hash_key, item.status)
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
            await self.status_changed(
                hash_key,
                fields=fields,
                previous_status=item.status,
            )
            return True
        return False


# ── 外观 ──────────────────────────────────────


class MagnetItemTransitions:
    """Applies Magnet item state changes and publishes matching events.

    Facade that delegates to 3 domain objects (Discovery, Classification,
    Download). Preserves the original public API so callers are unaffected.
    """

    def __init__(self, store: ItemStore, bus: MessageBus):
        self._store = store
        self._bus = bus
        self._discovery = DiscoveryTransitions(store, bus)
        self._classification = ClassificationTransitions(store, bus)
        self._download = DownloadTransitions(store, bus)

    @property
    def discovery(self) -> DiscoveryTransitions:
        """发现域转换（供未来直接访问）"""
        return self._discovery

    @property
    def classification(self) -> ClassificationTransitions:
        """分类域转换（供未来直接访问）"""
        return self._classification

    @property
    def download(self) -> DownloadTransitions:
        """下载域转换（供未来直接访问）"""
        return self._download

    # ── 发现域（委托）──

    async def found(self, item: MagnetItem) -> bool:
        return await self._discovery.found(item)

    async def clipboard_found(self, item: MagnetItem) -> bool:
        return await self._discovery.clipboard_found(item)

    # ── 分类域（委托）──

    async def classification_started(self, hash_key: str):
        await self._classification.started(hash_key)

    async def classified(self, hash_key: str, result: dict):
        await self._classification.classified(hash_key, result)

    async def classification_failed(self, hash_key: str, error_msg: str):
        await self._classification.failed(hash_key, error_msg)

    async def manually_classified(self, hash_key: str, category: str) -> bool:
        return await self._classification.manually_classified(hash_key, category)

    # ── 下载域（委托）──

    async def download_submitting(self, hash_key: str):
        await self._download.submitting(hash_key)

    async def download_submitted(self, hash_key: str):
        await self._download.submitted(hash_key)

    async def download_failed(self, hash_key: str, error_msg: str):
        await self._download.failed(hash_key, error_msg)

    async def download_removed(self, hash_key: str, previous_status: TaskStatus | None):
        await self._download.removed(hash_key, previous_status)

    async def download_status_changed(
        self,
        hash_key: str,
        *,
        fields: dict,
        previous_status: TaskStatus | None,
    ):
        await self._download.status_changed(hash_key, fields=fields, previous_status=previous_status)

    async def download_state_changed(
        self,
        hash_key: str,
        previous_status: TaskStatus | None = None,
    ):
        await self._download.state_changed(hash_key, previous_status)

    async def reconcile_download_snapshot(
        self,
        hash_key: str,
        item: MagnetItem,
        torrent: dict | None,
        *,
        was_removed: bool = False,
    ) -> bool:
        return await self._download.reconcile_snapshot(
            hash_key, item, torrent, was_removed=was_removed
        )

    # ── 共享操作 ──

    async def cleared(self) -> int:
        """清空全部 + ITEMS_CLEARED

        store.clear() 现在原子化地返回清空前的条目数，消除了原先
        count→clear 之间的 check-then-act 竞态窗口。
        """
        count = self._store.clear()
        await self._bus.emit(Event(EventType.ITEMS_CLEARED, {"type": "items_cleared"}))
        if self._store.count > 0:
            log.warning("cleared() 清空后 store 仍有 %d 个条目（并发写入）", self._store.count)
        return count
