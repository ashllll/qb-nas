"""Application runtime assembly helpers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from magnet_harvester.api.websocket import WSBroadcaster
from magnet_harvester.bus import MessageBus
from magnet_harvester.classifier import LocalClassifier
from magnet_harvester.config import settings
from magnet_harvester.context.app_context import AppContext
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.services.agent_tools import ToolExecutor
from magnet_harvester.services.qbit_sync import QBitSyncLoop
from magnet_harvester.services.stats import SystemStats
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.utils.bg_tasks import BGTaskManager


@dataclass
class AppRuntime:
    ctx: AppContext
    sync_loop: QBitSyncLoop

    async def start(self):
        await self.ctx.crawler.start()
        await self.sync_loop.start()

    async def stop(self):
        await self.sync_loop.stop()
        await self.ctx.crawler.stop()
        await self.ctx.qbit.close()


def build_runtime() -> AppRuntime:
    qbit_lock = asyncio.Lock()
    crawler = MagnetCrawler(config=settings.crawler)
    qbit = QBittorrentClient(config=settings.qbit)
    classifier = LocalClassifier()
    store = InMemoryItemStore()
    bus = MessageBus()
    bg_manager = BGTaskManager()
    pipeline = HarvestPipeline(
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        store=store,
        bus=bus,
        task_manager=bg_manager,
    )
    app_stats = SystemStats()
    broadcaster = WSBroadcaster(bus=bus, store=store)
    sync_loop = QBitSyncLoop(
        qbit_client=qbit,
        store=store,
        bus=bus,
        task_manager=bg_manager,
    )
    tool_executor = ToolExecutor(store=store, pipeline=pipeline, bus=bus, task_manager=bg_manager)

    ctx = AppContext(
        store=store,
        bus=bus,
        pipeline=pipeline,
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        stats=app_stats,
        bg_manager=bg_manager,
        broadcaster=broadcaster,
        tool_executor=tool_executor,
        qbit_lock=qbit_lock,
    )
    return AppRuntime(ctx=ctx, sync_loop=sync_loop)
