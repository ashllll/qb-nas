"""
Application context — dependency container for Magnet Harvester.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol

from fastapi import Request

from magnet_harvester.config import Settings, settings as default_settings
from magnet_harvester.qbit_client import QBittorrentClient

if TYPE_CHECKING:
    from magnet_harvester.store import ItemStore
    from magnet_harvester.bus import MessageBus
    from magnet_harvester.pipeline import HarvestPipeline
    from magnet_harvester.crawler import MagnetCrawler
    from magnet_harvester.classifier import LocalClassifier
    from magnet_harvester.transitions import MagnetItemTransitions


log = logging.getLogger(__name__)


class StatsTracker(Protocol):
    def record_crawl(self) -> None: ...
    def record_download(self) -> None: ...
    def record_api_call(self) -> None: ...
    def as_dict(self) -> dict: ...


class BackgroundTaskSpawner(Protocol):
    def create(self, coro, name: str | None = None): ...
    def get_task(self, task_id: str) -> dict | None: ...
    async def shutdown(self) -> None: ...


class BroadcasterLike(Protocol):
    @property
    def active_count(self) -> int: ...
    async def handle_connection(self, ws) -> None: ...


class UserActionExecutorLike(Protocol):
    async def start_crawl(
        self, url: str, *, depth: int = 1, auto_download: bool = False
    ) -> dict: ...
    async def download(
        self, hashes: list[str], *, task_name: str = "download_selected"
    ) -> dict: ...
    async def download_pending(self) -> dict: ...
    async def reclassify(self, hashes: list[str]) -> dict: ...
    async def manually_reclassify(self, hash_prefix: str, category: str) -> dict: ...
    async def clear_items(self) -> dict: ...


class QBitSyncLike(Protocol):
    async def replace_qbit_client(self, new_qbit) -> None: ...


class QBitRuntimeLike(Protocol):
    async def replace_qbit(self, new_qbit) -> None: ...
    async def replace_qbit_config(
        self,
        host: str | None,
        username: str | None,
        password: str | None,
    ) -> dict: ...


class ClipboardMonitorLike(Protocol):
    @property
    def is_running(self) -> bool: ...
    @property
    def magnet_count(self) -> int: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class ErrorHandlerLike(Protocol):
    def get_error_stats(self) -> dict: ...
    def clear_resolved(self): ...


class ObservabilityLike(Protocol):
    async def system_status(self) -> dict: ...
    async def health(self) -> dict: ...
    def api_stats(self) -> dict: ...


class ItemQueryLike(Protocol):
    def get_stats(self) -> dict: ...
    def list_items(
        self,
        *,
        category: str | None = None,
        status: str = "all",
        limit: int = 20,
    ) -> dict: ...
    def page_items(
        self,
        *,
        category: str | None = None,
        status: str = "all",
        limit: int = 100,
        offset: int = 0,
    ) -> dict: ...
    def search_items(self, *, query: str, limit: int = 20) -> dict: ...


# ── 语义域子容器 ──────────────────────────────


@dataclass
class CoreServices:
    """基础设施层：所有服务的公共依赖。"""

    store: ItemStore
    bus: MessageBus
    pipeline: HarvestPipeline
    crawler: MagnetCrawler
    classifier: LocalClassifier
    qbit: QBittorrentClient


@dataclass
class AppServices:
    """用户面向层：路由和 WebSocket 使用的服务。"""

    action_executor: UserActionExecutorLike | None = None
    observability: ObservabilityLike | None = None
    item_queries: ItemQueryLike | None = None
    clipboard_monitor: ClipboardMonitorLike | None = None
    broadcaster: BroadcasterLike | None = None


@dataclass
class RuntimeState:
    """运行时协调层：生命周期、热替换、错误处理。"""

    api_key: str = ""
    stats: StatsTracker | None = None
    bg_manager: BackgroundTaskSpawner | None = None
    qbit_lock: asyncio.Lock | None = None
    error_handler: ErrorHandlerLike | None = None
    item_transitions: MagnetItemTransitions | None = None
    qbit_sync: QBitSyncLike | None = None
    qbit_runtime: QBitRuntimeLike | None = None


# ── AppContext — 薄外观 + 向后兼容属性 ──────────


@dataclass
class AppContext:
    """应用依赖容器（外观）。

    内部持有 3 个语义域子容器，通过属性提供向后兼容访问。
    """

    core: CoreServices
    app_services: AppServices = field(default_factory=AppServices)
    runtime: RuntimeState = field(default_factory=RuntimeState)

    def __post_init__(self):
        if self.app_services is None:
            self.app_services = AppServices()
        if self.runtime is None:
            self.runtime = RuntimeState()

    # ── 向后兼容属性 ──

    @property
    def store(self) -> ItemStore:
        return self.core.store

    @property
    def bus(self) -> MessageBus:
        return self.core.bus

    @property
    def pipeline(self) -> HarvestPipeline:
        return self.core.pipeline

    @pipeline.setter
    def pipeline(self, value: HarvestPipeline) -> None:
        self.core.pipeline = value

    @property
    def crawler(self) -> MagnetCrawler:
        return self.core.crawler

    @property
    def classifier(self) -> LocalClassifier:
        return self.core.classifier

    @property
    def qbit(self) -> QBittorrentClient:
        return self.core.qbit

    @qbit.setter
    def qbit(self, value: QBittorrentClient) -> None:
        self.core.qbit = value

    @property
    def api_key(self) -> str:
        return self.runtime.api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self.runtime.api_key = value

    @property
    def stats(self) -> StatsTracker | None:
        return self.runtime.stats

    @property
    def bg_manager(self) -> BackgroundTaskSpawner | None:
        return self.runtime.bg_manager

    @property
    def broadcaster(self) -> BroadcasterLike | None:
        return self.app_services.broadcaster

    @property
    def action_executor(self) -> UserActionExecutorLike | None:
        return self.app_services.action_executor

    @action_executor.setter
    def action_executor(self, value: UserActionExecutorLike | None) -> None:
        self.app_services.action_executor = value

    @property
    def qbit_sync(self) -> QBitSyncLike | None:
        return self.runtime.qbit_sync

    @property
    def qbit_runtime(self) -> QBitRuntimeLike | None:
        return self.runtime.qbit_runtime

    @qbit_runtime.setter
    def qbit_runtime(self, value: QBitRuntimeLike | None) -> None:
        self.runtime.qbit_runtime = value

    @property
    def qbit_lock(self) -> asyncio.Lock | None:
        return self.runtime.qbit_lock

    @property
    def clipboard_monitor(self) -> ClipboardMonitorLike | None:
        return self.app_services.clipboard_monitor

    @property
    def error_handler(self) -> ErrorHandlerLike | None:
        return self.runtime.error_handler

    @property
    def item_transitions(self) -> MagnetItemTransitions | None:
        return self.runtime.item_transitions

    @property
    def observability(self) -> ObservabilityLike | None:
        return self.app_services.observability

    @observability.setter
    def observability(self, value: ObservabilityLike | None) -> None:
        self.app_services.observability = value

    @property
    def item_queries(self) -> ItemQueryLike | None:
        return self.app_services.item_queries

    @item_queries.setter
    def item_queries(self, value: ItemQueryLike | None) -> None:
        self.app_services.item_queries = value


@dataclass
class QBitReplacementTarget:
    """Narrow seam for hot-swapping the active qBittorrent adapter.

    Does not hold the full AppContext; only the lock, callbacks, and
    optional dependents needed to coordinate a replacement.
    """

    get_qbit: Callable[[], QBittorrentClient | None]
    set_qbit: Callable[[QBittorrentClient | None], None]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    qbit_sync: QBitSyncLike | None = None
    pipeline: "HarvestPipeline" | None = None

    @classmethod
    def from_context(cls, ctx: AppContext) -> "QBitReplacementTarget":
        return cls(
            lock=ctx.qbit_lock or asyncio.Lock(),
            get_qbit=lambda: ctx.qbit,
            set_qbit=lambda value: setattr(ctx, "qbit", value),
            qbit_sync=ctx.qbit_sync,
            pipeline=ctx.pipeline,
        )

    async def replace(self, new_qbit: QBittorrentClient) -> None:
        """Hot-swap the active qBittorrent adapter and update dependents.

        Implements verify-then-commit:
        1. Verify   — confirm all dependents can accept the new client.
        2. Commit   — update dependents first, then the primary reference.
        3. Cleanup  — close old transport with exception isolation.
        """
        async with self.lock:
            old_qbit = self.get_qbit()

            # Phase 1: Verify — if any dependent update ever gains
            # validation logic (e.g. pre-flight health checks), add it
            # here so the system stays consistent on failure.

            # Phase 2: Commit — dependents first, then primary reference.
            # All three steps are trivial assignments today; if any step
            # fails the exception propagates and old_qbit stays active.
            if self.pipeline is not None:
                self.pipeline.replace_download_phase(new_qbit)
            if self.qbit_sync is not None:
                await self.qbit_sync.replace_qbit_client(new_qbit)
            self.set_qbit(new_qbit)

            # Phase 3: Cleanup — close old transport without aborting
            # the replacement.  Never close a client that was just
            # installed (guard against caller passing ctx.qbit as both
            # old and new).
            if old_qbit is not None and old_qbit is not new_qbit:
                try:
                    await old_qbit.close()
                except Exception:
                    log.exception(
                        "热替换：关闭旧 qBittorrent 客户端失败"
                    )


@dataclass
class QBitRuntime:
    ctx: AppContext
    settings: Settings = field(default_factory=lambda: default_settings)
    client_factory: type[QBittorrentClient] = field(default_factory=lambda: QBittorrentClient)
    replacement_target: QBitReplacementTarget | None = None
    config_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def replace_qbit(self, new_qbit):
        target = self.replacement_target or QBitReplacementTarget.from_context(self.ctx)
        await target.replace(new_qbit)

    async def replace_qbit_config(
        self,
        host: str | None,
        username: str | None,
        password: str | None,
    ) -> dict:
        """Validate, persist, and hot-swap the qBittorrent configuration.

        Returns:
            {"status": "ok", "connected": True} on success.
            {"status": "failed", "connected": False} when the new endpoint is unreachable.

        Raises:
            ValueError: if the candidate config is invalid (maps to HTTP 422).
            OSError: if persisting the config fails (maps to HTTP 500).
        """
        async with self.config_lock:
            candidate = self.settings.build_qbit_config(
                host=host,
                username=username,
                password=password,
            )
            new_qbit = self.client_factory(config=candidate)
            if not await new_qbit.ping():
                await new_qbit.close()
                return {"status": "failed", "connected": False}

            try:
                self.settings.persist_qbit_config(candidate)
            except OSError:
                await new_qbit.close()
                raise

            self.settings.commit_qbit_config(candidate)
            await self.replace_qbit(new_qbit)
            return {"status": "ok", "connected": True}


RuntimeContext = QBitRuntime


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx
