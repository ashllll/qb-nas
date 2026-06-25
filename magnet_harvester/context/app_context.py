"""
Application context — dependency container for Magnet Harvester.
"""

from __future__ import annotations

import asyncio
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


@dataclass
class AppContext:
    store: ItemStore
    bus: MessageBus
    pipeline: HarvestPipeline
    crawler: MagnetCrawler
    classifier: LocalClassifier
    qbit: QBittorrentClient
    api_key: str = ""
    stats: StatsTracker | None = None
    bg_manager: BackgroundTaskSpawner | None = None
    broadcaster: BroadcasterLike | None = None
    action_executor: UserActionExecutorLike | None = None
    qbit_sync: QBitSyncLike | None = None
    qbit_runtime: QBitRuntimeLike | None = None
    qbit_lock: asyncio.Lock | None = None
    clipboard_monitor: ClipboardMonitorLike | None = None
    error_handler: ErrorHandlerLike | None = None
    item_transitions: MagnetItemTransitions | None = None
    observability: ObservabilityLike | None = None
    item_queries: ItemQueryLike | None = None


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
        """Hot-swap the active qBittorrent adapter and update dependents."""
        async with self.lock:
            old_qbit = self.get_qbit()
            if self.qbit_sync is not None:
                await self.qbit_sync.replace_qbit_client(new_qbit)
            self.set_qbit(new_qbit)
            try:
                if self.pipeline is not None:
                    self.pipeline.replace_download_phase(new_qbit)
            finally:
                if old_qbit is not None:
                    await old_qbit.close()


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
