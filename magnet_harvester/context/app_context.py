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
    def clear_all(self): ...


class ObservabilityLike(Protocol):
    async def system_status(self) -> dict: ...
    async def health(self) -> dict: ...
    async def api_stats(self) -> dict: ...
    def replace_qbit_client(self, new_qbit) -> None: ...


class ItemQueryLike(Protocol):
    async def get_stats(self) -> dict: ...
    async def list_items(
        self,
        *,
        category: str | None = None,
        status: str = "all",
        limit: int = 20,
    ) -> dict: ...
    async def page_items(
        self,
        *,
        category: str | None = None,
        status: str = "all",
        limit: int = 100,
        offset: int = 0,
    ) -> dict: ...
    async def search_items(self, *, query: str, limit: int = 20) -> dict: ...


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
    qbit_sync: QBitSyncLike | None = None
    qbit_runtime: QBitRuntimeLike | None = None


# ── AppContext — semantic container root ─────────


@dataclass
class AppContext:
    """Root holding the core, application, and runtime containers."""

    core: CoreServices
    app_services: AppServices = field(default_factory=AppServices)
    runtime: RuntimeState = field(default_factory=RuntimeState)


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
    observability: ObservabilityLike | None = None

    @classmethod
    def from_context(cls, ctx: AppContext) -> "QBitReplacementTarget":
        return cls(
            lock=ctx.runtime.qbit_lock or asyncio.Lock(),
            get_qbit=lambda: ctx.core.qbit,
            set_qbit=lambda value: setattr(ctx.core, "qbit", value),
            qbit_sync=ctx.runtime.qbit_sync,
            pipeline=ctx.core.pipeline,
            observability=ctx.app_services.observability,
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
            # If any dependent fails after an earlier one changed, restore
            # the old adapter before propagating the failure.
            try:
                await asyncio.wait_for(
                    self._commit(new_qbit),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                await self._rollback(old_qbit)
                log.error("热替换：commit 阶段超时（30s），中止替换")
                raise RuntimeError("热替换 qBittorrent 客户端超时") from None
            except Exception:
                await self._rollback(old_qbit)
                raise

            # Phase 3: Cleanup — close old transport without aborting
            # the replacement.  Never close a client that was just
            # installed (guard against caller passing ctx.core.qbit as both
            # old and new).
            if old_qbit is not None and old_qbit is not new_qbit:
                try:
                    await asyncio.wait_for(
                        old_qbit.close(),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    log.error("热替换：关闭旧 qBittorrent 客户端超时")
                except Exception:
                    log.exception("热替换：关闭旧 qBittorrent 客户端失败")

    async def _commit(self, new_qbit: QBittorrentClient) -> None:
        """Phase 2: update dependents then primary reference."""
        if self.pipeline is not None:
            self.pipeline.replace_download_phase(new_qbit)
        if self.qbit_sync is not None:
            await self.qbit_sync.replace_qbit_client(new_qbit)
        if self.observability is not None:
            self.observability.replace_qbit_client(new_qbit)
        self.set_qbit(new_qbit)

    async def _rollback(self, old_qbit: QBittorrentClient | None) -> None:
        """Best-effort rollback after a failed replacement commit."""
        if old_qbit is not None and self.pipeline is not None:
            try:
                self.pipeline.replace_download_phase(old_qbit)
            except Exception:
                log.exception("热替换：回滚 pipeline qBittorrent 客户端失败")
        if old_qbit is not None and self.qbit_sync is not None:
            try:
                await self.qbit_sync.replace_qbit_client(old_qbit)
            except Exception:
                log.exception("热替换：回滚 qB 同步客户端失败")
        if old_qbit is not None and self.observability is not None:
            try:
                self.observability.replace_qbit_client(old_qbit)
            except Exception:
                log.exception("热替换：回滚观测 qBittorrent 客户端失败")
        self.set_qbit(old_qbit)


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
            old_qbit = self.ctx.core.qbit
            old_config = getattr(old_qbit, "config", None)
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

            try:
                await self.replace_qbit(new_qbit)
                if self.ctx.core.qbit is not new_qbit:
                    raise RuntimeError("热替换 qBittorrent 客户端失败")
            except Exception as exc:
                await new_qbit.close()
                if old_config is not None:
                    try:
                        self.settings.persist_qbit_config(old_config)
                        self.settings.commit_qbit_config(old_config)
                    except OSError:
                        log.exception("热替换失败后回滚 qBittorrent 配置持久化失败")
                if isinstance(exc, RuntimeError):
                    raise
                raise RuntimeError("热替换 qBittorrent 客户端失败") from exc

            self.settings.commit_qbit_config(candidate)
            return {"status": "ok", "connected": True}


RuntimeContext = QBitRuntime


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx
