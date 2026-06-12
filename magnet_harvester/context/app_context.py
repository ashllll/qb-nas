"""
Application context — dependency container for Magnet Harvester.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from fastapi import Request

if TYPE_CHECKING:
    from magnet_harvester.store import ItemStore
    from magnet_harvester.bus import MessageBus
    from magnet_harvester.pipeline import HarvestPipeline
    from magnet_harvester.crawler import MagnetCrawler
    from magnet_harvester.classifier import LocalClassifier
    from magnet_harvester.qbit_client import QBittorrentClient


class StatsTracker(Protocol):
    def record_crawl(self) -> None: ...
    def record_download(self) -> None: ...
    def record_api_call(self) -> None: ...
    def as_dict(self) -> dict: ...


class BackgroundTaskSpawner(Protocol):
    def create(self, coro, name: str | None = None): ...


class BroadcasterLike(Protocol):
    @property
    def active_count(self) -> int: ...
    async def handle_connection(self, ws) -> None: ...


class ToolExecutorLike(Protocol):
    async def execute(self, name: str, inp: dict) -> dict: ...


class QBitSyncLike(Protocol):
    async def replace_qbit_client(self, new_qbit) -> None: ...


@dataclass
class AppContext:
    store: ItemStore
    bus: MessageBus
    pipeline: HarvestPipeline
    crawler: MagnetCrawler
    classifier: LocalClassifier
    qbit: QBittorrentClient
    stats: StatsTracker | None = None
    bg_manager: BackgroundTaskSpawner | None = None
    broadcaster: BroadcasterLike | None = None
    tool_executor: ToolExecutorLike | None = None
    qbit_sync: QBitSyncLike | None = None
    qbit_lock: asyncio.Lock | None = None


@dataclass
class RuntimeContext:
    ctx: AppContext

    async def replace_qbit(self, new_qbit):
        old_qbit = self.ctx.qbit
        if self.ctx.qbit_sync is not None:
            await self.ctx.qbit_sync.replace_qbit_client(new_qbit)
        self.ctx.qbit = new_qbit
        if self.ctx.pipeline is not None:
            self.ctx.pipeline.replace_download_phase(new_qbit)
        if old_qbit is not None:
            await old_qbit.close()


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx
