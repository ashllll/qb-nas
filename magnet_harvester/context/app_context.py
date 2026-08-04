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


class QBitClientLike(Protocol):
    """QBitRuntime 热替换所需的最小 qBittorrent 客户端接口。"""

    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


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

    def replacement_target(self) -> QBitReplacementTarget:
        """提取 qBittorrent 热替换所需的最窄依赖集合。

        QBitRuntime 只应依赖该 target，不再持有完整 AppContext。
        on_qbit_replaced 回调负责把新客户端写回 core.qbit，
        使“替换已生效”对主容器可见。
        """
        return QBitReplacementTarget(
            qbit=self.core.qbit,
            pipeline=self.core.pipeline,
            qbit_sync=self.runtime.qbit_sync,
            observability=self.app_services.observability,
            on_qbit_replaced=lambda new_qbit: setattr(self.core, "qbit", new_qbit),
        )


@dataclass
class QBitReplacementTarget:
    """热替换 qBittorrent 客户端所需的最窄依赖集合。

    字段说明：
    - qbit: 当前生效的 qBittorrent 客户端（替换操作从它回滚）。
    - pipeline / qbit_sync / observability: 需要对齐新客户端的运行时依赖。
    - on_qbit_replaced: 可选回调，替换成功后写回主容器（如 core.qbit）。
    """

    qbit: QBitClientLike | None = None
    pipeline: HarvestPipeline | None = None
    qbit_sync: QBitSyncLike | None = None
    observability: ObservabilityLike | None = None
    on_qbit_replaced: Callable[[QBittorrentClient], None] | None = None


@dataclass
class QBitRuntime:
    """qBittorrent 配置热替换适配器 — 只依赖 QBitReplacementTarget。

    不再持有完整 AppContext（见 AppContext.replacement_target）。
    用 QBitRuntime.from_context(ctx) 从容器构建。
    """

    target: QBitReplacementTarget
    settings: Settings = field(default_factory=lambda: default_settings)
    client_factory: type[QBittorrentClient] = field(default_factory=lambda: QBittorrentClient)
    transaction_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def from_context(cls, ctx: AppContext, **kwargs) -> QBitRuntime:
        """从 AppContext 构建，只提取热替换所需的最窄依赖。"""
        return cls(target=ctx.replacement_target(), **kwargs)

    async def replace_qbit(self, new_qbit: QBittorrentClient) -> None:
        async with self.transaction_lock:
            await self._replace_runtime(new_qbit)

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
        async with self.transaction_lock:
            old_qbit = self.target.qbit
            old_config = getattr(old_qbit, "config", None)
            candidate = self.settings.build_qbit_config(
                host=host,
                username=username,
                password=password,
            )
            new_qbit = self.client_factory(config=candidate)
            try:
                connected = await new_qbit.ping()
            except asyncio.CancelledError:
                await new_qbit.close()
                raise
            if not connected:
                await new_qbit.close()
                return {"status": "failed", "connected": False}

            try:
                self.settings.persist_qbit_config(candidate)
            except OSError:
                await new_qbit.close()
                raise

            try:
                await self._replace_runtime(new_qbit)
                if self.target.qbit is not new_qbit:
                    raise RuntimeError("热替换 qBittorrent 客户端失败")
            except (Exception, asyncio.CancelledError) as exc:
                await new_qbit.close()
                if old_config is not None:
                    try:
                        self.settings.persist_qbit_config(old_config)
                        self.settings.commit_qbit_config(old_config)
                    except OSError:
                        log.exception("热替换失败后回滚 qBittorrent 配置持久化失败")
                if isinstance(exc, (RuntimeError, asyncio.CancelledError)):
                    raise
                raise RuntimeError("热替换 qBittorrent 客户端失败") from exc

            self.settings.commit_qbit_config(candidate)
            return {"status": "ok", "connected": True}

    async def _replace_runtime(self, new_qbit: QBittorrentClient) -> None:
        """Align every runtime dependent and commit the primary adapter."""
        old_qbit = self.target.qbit
        try:
            await asyncio.wait_for(self._commit_runtime(new_qbit), timeout=30.0)
        except asyncio.TimeoutError:
            await self._rollback_runtime(old_qbit)
            raise RuntimeError("热替换 qBittorrent 客户端超时") from None
        except asyncio.CancelledError:
            await self._rollback_runtime(old_qbit)
            raise
        except Exception:
            await self._rollback_runtime(old_qbit)
            raise

        if old_qbit is not None and old_qbit is not new_qbit:
            try:
                await asyncio.wait_for(old_qbit.close(), timeout=10.0)
            except asyncio.TimeoutError:
                log.error("关闭旧 qBittorrent 客户端超时")
            except Exception:
                log.exception("关闭旧 qBittorrent 客户端失败")

    async def _commit_runtime(self, new_qbit: QBittorrentClient) -> None:
        pipeline = self.target.pipeline
        qbit_sync = self.target.qbit_sync
        observability = self.target.observability
        if pipeline is not None:
            pipeline.replace_download_phase(new_qbit)
        if qbit_sync is not None:
            await qbit_sync.replace_qbit_client(new_qbit)
        if observability is not None:
            observability.replace_qbit_client(new_qbit)
        self.target.qbit = new_qbit
        if self.target.on_qbit_replaced is not None:
            self.target.on_qbit_replaced(new_qbit)

    async def _rollback_runtime(self, old_qbit: QBittorrentClient | None) -> None:
        """Best-effort restoration after a dependent rejects the candidate."""
        if old_qbit is None:
            return
        pipeline = self.target.pipeline
        qbit_sync = self.target.qbit_sync
        observability = self.target.observability
        if pipeline is not None:
            try:
                pipeline.replace_download_phase(old_qbit)
            except Exception:
                log.exception("回滚 pipeline qBittorrent 客户端失败")
        if qbit_sync is not None:
            try:
                await qbit_sync.replace_qbit_client(old_qbit)
            except Exception:
                log.exception("回滚 qB 同步客户端失败")
        if observability is not None:
            try:
                observability.replace_qbit_client(old_qbit)
            except Exception:
                log.exception("回滚可观测 qBittorrent 客户端失败")
        self.target.qbit = old_qbit
        if self.target.on_qbit_replaced is not None:
            self.target.on_qbit_replaced(old_qbit)


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx
