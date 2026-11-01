"""
Magnet item lifecycle modules apply state changes and publish events.

This module encapsulates the knowledge of what state transitions exist for a
Magnet item and which events each transition should publish.

Organized into three deep modules: Discovery, Classification, and Download.
Callers depend only on the lifecycle module they use.

Used by HarvestPipeline during crawl→classify→download orchestration.
"""

from __future__ import annotations

import asyncio
import logging

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.qbit_client.mapper import TorrentStatusMapper
from magnet_harvester.utils.serializers import item_payload
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import AsyncItemStore, ItemStore

log = logging.getLogger(__name__)


# ── 共享事件发射辅助 ──────────────────────────


class _TransitionBase:
    """域对象基类：封装 store/bus 依赖和事件发射辅助方法。"""

    def __init__(self, store: ItemStore, bus: MessageBus):
        self._store = store
        self._bus = bus

    async def _emit_item_changed(self, hash_key: str) -> None:
        """发射 STORE_CHANGED 事件（如果 item 存在）。"""
        item = await self._store.get(hash_key)
        if item is not None:
            await self._bus.emit(Event(EventType.STORE_CHANGED, {"item": item_payload(item)}))

    async def _emit_download_result(
        self, hash_key: str, previous_status: TaskStatus | None = None
    ) -> None:
        """发射 DOWNLOAD_RESULT 事件（状态变化或进入终态时）。"""
        item = await self._store.get(hash_key)
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
            item = await self._store.get(hash_key)
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
                        "updated_at": item.updated_at.isoformat(),
                    },
                )
            )


# ── 域对象 ────────────────────────────────────


class DiscoveryTransitions(_TransitionBase):
    """发现域：磁力链接发现相关的状态转换。"""

    async def found(self, item: MagnetItem) -> bool:
        if not await self._store.add(item):
            return False
        try:
            await self._bus.emit(Event(EventType.MAGNET_FOUND, {"item": item.model_dump()}))
        except Exception:
            log.warning("MAGNET_FOUND emit 失败，item 已持久化 hash=%s", item.hash, exc_info=True)
        return True

    async def cleared(self) -> int:
        """Clear all Magnet items and publish the collection change."""
        count = await self._store.clear()
        await self._bus.emit(Event(EventType.ITEMS_CLEARED, {}))
        remaining = await self._store.count()
        if remaining > 0:
            log.warning("cleared() 后 store 仍有 %d 个条目（并发写入）", remaining)
        return count

    async def removed(self, hash_key: str) -> bool:
        """删除单条资源记录并通知界面立即移除。

        只删资源库里的这一行，**不动 qBittorrent 里的种子**：用户真实下载队列
        不归资源库管（约定：测试条目用完删掉，但不许动 qB 队列）。
        """
        if not await self._store.remove(hash_key):
            return False
        try:
            await self._bus.emit(Event(EventType.ITEM_REMOVED, {"hash": hash_key}))
        except Exception:
            log.warning("ITEM_REMOVED emit 失败，item 已删除 hash=%s", hash_key, exc_info=True)
        return True


class ClassificationTransitions(_TransitionBase):
    """分类域：分类生命周期相关的状态转换。"""

    async def started(self, hash_key: str):
        """进入分类中。

        只允许从 pending/error 进入：分类与「用户点下载」「qB 状态同步」是并发的，
        读一次再无条件写回会把别人刚写好的 adding/downloading/paused 覆盖掉。
        """
        changed = await self._store.update_if_status(
            hash_key,
            {TaskStatus.pending, TaskStatus.error},
            status=TaskStatus.classifying,
            error_msg=None,
        )
        if not changed:
            return
        await self._emit_item_changed(hash_key)

    async def classified(self, hash_key: str, result: dict):
        category = result.get("category", "其他")
        changed = await self._store.update_if_status(
            hash_key,
            {TaskStatus.classifying},
            category=category,
            save_path=result.get("save_path", ""),
            status=TaskStatus.pending,
            progress=0.0,
            torrent_state=None,
            error_msg=None,
        )
        if not changed:
            log.warning("classified 跳过 %s（不在 classifying，可能已被并发删除或转入下载）", hash_key)
            return
        # 重新读取以拿到 update 后的 updated_at（作为事件版本号）
        item = await self._store.get(hash_key)
        if item is None:
            log.warning("classified emit 前条目已被并发删除 %s", hash_key)
            return
        await self._bus.emit(
            Event(
                EventType.CLASSIFY_DONE,
                {
                    "hash": hash_key,
                    "category": category,
                    "confidence": result.get("confidence", ""),
                    "reason": result.get("reason", ""),
                    "updated_at": item.updated_at.isoformat(),
                },
            )
        )
        await self._emit_item_changed(hash_key)

    async def failed(self, hash_key: str, error_msg: str):
        """分类失败时回退状态到 pending，以便后续重试。"""
        changed = await self._store.update_if_status(
            hash_key,
            {TaskStatus.classifying},
            status=TaskStatus.pending,
            error_msg=error_msg,
        )
        if not changed:
            return
        await self._emit_item_changed(hash_key)

    async def manually_classified(self, hash_key: str, category: str) -> bool:
        """手动分类：更新 + CLASSIFY_DONE + emit_item_changed"""
        item = await self._store.get(hash_key)
        if item is None or item.status not in {TaskStatus.pending, TaskStatus.error}:
            return False
        # 只更新分类字段，避免先读取再写回 save_path 时覆盖并发中的路径变更。
        if not await self._store.update(hash_key, category=category):
            return False
        item = await self._store.get(hash_key)
        if item is None:
            log.warning("manually_classified emit 前条目已被并发删除 %s", hash_key)
            return
        await self._bus.emit(
            Event(
                EventType.CLASSIFY_DONE,
                {
                    "hash": hash_key,
                    "category": category,
                    "confidence": "manual",
                    "reason": "手动修改",
                    "updated_at": item.updated_at.isoformat(),
                },
            )
        )
        await self._emit_item_changed(hash_key)
        return True


class DownloadTransitions(_TransitionBase):
    """下载域：下载生命周期相关的状态转换。"""

    # 启动时一次最多回收多少条「卡在加入中」的记录；正常机器远到不了这个量。
    RECOVER_LIMIT = 1000
    INTERRUPTED_ADDING_MSG = (
        "上次「加入 qB」在服务重启/退出时被中断，结果未知："
        "qB 里已经有这条的话，下一轮同步会自动长回下载状态；没有就重新点「下载」"
    )

    def __init__(self, store: ItemStore, bus: MessageBus):
        super().__init__(store, bus)
        self._submit_lock = asyncio.Lock()

    async def recover_interrupted(self) -> int:
        """服务启动时调用：把「正在加入 qB」的半截条目退回可重试状态。

        ``adding`` 只出现在 qB add 调用进行中的那一小段：进程在这中间没了（容器
        重启、kill -9、部署重建），条目就永远停在「添加中」——qB 同步查不到种子
        不会管它，``download``/``reclassify`` 又都不收 adding，界面没有任何重试入口。
        这里把它改成 ``error`` 并写清原因：同步循环认得 error（qB 里真有这条会自己
        长回去），用户也能直接重新点下载。
        """
        try:
            pending = await self._store.list(
                status=TaskStatus.adding.value, limit=self.RECOVER_LIMIT
            )
        except Exception:
            log.exception("读取「加入中」条目失败，跳过本次恢复")
            return 0

        recovered = 0
        for item in pending:
            if not await self._store.update_if_status(
                item.hash,
                {TaskStatus.adding},
                status=TaskStatus.error,
                error_msg=self.INTERRUPTED_ADDING_MSG,
                torrent_state="submit_interrupted",
            ):
                continue
            recovered += 1
            await self._emit_item_changed(item.hash)
        if recovered:
            log.warning(
                "上次运行有 %d 条「加入 qB」被中断，已退回可重试状态（%s）",
                recovered,
                self.INTERRUPTED_ADDING_MSG,
            )
        if len(pending) >= self.RECOVER_LIMIT:
            log.warning(
                "「加入中」条目达到回收上限 %d 条，其余留给 qB 同步处理",
                self.RECOVER_LIMIT,
            )
        return recovered

    async def submitting(self, hash_key: str) -> bool:
        async with self._submit_lock:
            item = await self._store.get(hash_key)
            if item is None:
                return False
            # 前置状态检查：只允许从 pending 或 error 状态转换到 adding
            if item.status not in {TaskStatus.pending, TaskStatus.error}:
                return False
            if not await self._store.update(
                hash_key,
                status=TaskStatus.adding,
                progress=0.0,
                torrent_state="submitting",
                error_msg=None,
            ):
                return False
        await self._emit_item_changed(hash_key)
        await self._bus.emit(Event(EventType.DOWNLOAD_START, {"hash": hash_key, "name": item.name}))
        return True

    async def submitted(self, hash_key: str):
        item = await self._store.get(hash_key)
        if item is None or item.status != TaskStatus.adding:
            return
        previous_status = item.status
        if not await self._store.update(
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
        item = await self._store.get(hash_key)
        if item is None:
            return
        previous_status = item.status
        # 前置状态检查：只允许从非终态转换到 error，已成功的种子不能被错误标记
        if item.status in {TaskStatus.success, TaskStatus.error, TaskStatus.skipped}:
            return
        if not await self._store.update(hash_key, status=TaskStatus.error, error_msg=error_msg):
            return
        try:
            await self._emit_item_changed(hash_key)
        except Exception:
            log.exception("download failure STORE_CHANGED 发送失败 hash=%s", hash_key)
        try:
            await self._emit_download_result(hash_key, previous_status=previous_status)
        except Exception:
            log.exception("download failure DOWNLOAD_RESULT 发送失败 hash=%s", hash_key)

    async def removed(self, hash_key: str, previous_status: TaskStatus | None):
        """种子已从 qBittorrent 中消失"""
        if not await self._store.update(
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
        if not await self._store.update(hash_key, **fields):
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

        try:
            mapped = TorrentStatusMapper.map(torrent)
        except Exception:
            log.warning("无法映射 torrent 状态，跳过 hash=%s", hash_key, exc_info=True)
            return False

        fields: dict = {}

        if mapped["status"] is None:
            # 增量里 state 还没到（字段未到 ≠ 状态未知）：只同步进度，
            # 不动 status / torrent_state / error_msg，更不许把正在下载的种子说成失败。
            if item.progress != mapped["progress"]:
                fields["progress"] = mapped["progress"]
            if fields:
                await self.status_changed(
                    hash_key,
                    fields=fields,
                    previous_status=item.status,
                )
                return True
            return False

        if item.status != mapped["status"]:
            fields["status"] = mapped["status"]
        if item.progress != mapped["progress"]:
            fields["progress"] = mapped["progress"]
        if item.torrent_state != mapped["torrent_state"]:
            fields["torrent_state"] = mapped["torrent_state"]
        mapped_error = mapped.get("error_msg")
        if mapped["status"] == TaskStatus.error and mapped_error:
            # qB 侧异常（missingFiles/error/unknown）透传真实原因，替代笼统提示
            if item.error_msg != mapped_error:
                fields["error_msg"] = mapped_error
        elif item.error_msg and mapped["status"] != TaskStatus.error:
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
    """兼容旧编排接口的生命周期外观，内部委托给三个领域转换器。"""

    def __init__(self, store: ItemStore, bus: MessageBus):
        self._store = store
        async_store = store if isinstance(store, AsyncItemStore) else AsyncItemStore(store)
        self._discovery = DiscoveryTransitions(async_store, bus)
        self._classification = ClassificationTransitions(async_store, bus)
        self._download = DownloadTransitions(async_store, bus)

    async def found(self, item: MagnetItem) -> bool:
        return await self._discovery.found(item)

    async def classification_started(self, hash_key: str):
        await self._classification.started(hash_key)

    async def classified(self, hash_key: str, result: dict):
        await self._classification.classified(hash_key, result)

    async def classification_failed(self, hash_key: str, error_msg: str):
        await self._classification.failed(hash_key, error_msg)

    async def manually_classified(self, hash_key: str, category: str) -> bool:
        return await self._classification.manually_classified(hash_key, category)

    async def download_submitting(self, hash_key: str) -> bool:
        return await self._download.submitting(hash_key)

    async def download_submitted(self, hash_key: str):
        await self._download.submitted(hash_key)

    async def download_failed(self, hash_key: str, error_msg: str):
        await self._download.failed(hash_key, error_msg)

    async def download_removed(self, hash_key: str, previous_status: TaskStatus | None):
        await self._download.removed(hash_key, previous_status)

    async def download_status_changed(
        self, hash_key: str, *, fields: dict, previous_status: TaskStatus | None
    ):
        await self._download.status_changed(
            hash_key, fields=fields, previous_status=previous_status
        )

    async def download_state_changed(
        self, hash_key: str, previous_status: TaskStatus | None = None
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

    async def cleared(self) -> int:
        return await self._discovery.cleared()
