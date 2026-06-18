"""Application runtime assembly helpers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from magnet_harvester.api.websocket import WSBroadcaster
from magnet_harvester.bus import MessageBus
from magnet_harvester.classifier import LocalClassifier
from magnet_harvester.config import settings
from magnet_harvester.context.app_context import AppContext, QBitRuntime
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.errors import error_handler
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.services.clipboard_monitor import ClipboardMonitor
from magnet_harvester.services.qbit_sync import QBitSyncLoop
from magnet_harvester.services.stats import SystemStats
from magnet_harvester.services.user_actions import UserActionExecutor
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
        if self.ctx.bg_manager is not None:
            await self.ctx.bg_manager.shutdown()
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
    item_transitions = MagnetItemTransitions(store=store, bus=bus)
    pipeline = HarvestPipeline(
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        store=store,
        bus=bus,
        task_manager=bg_manager,
        transitions=item_transitions,
    )
    app_stats = SystemStats()
    broadcaster = WSBroadcaster(bus=bus, store=store)
    action_executor = UserActionExecutor(
        store=store,
        pipeline=pipeline,
        task_manager=bg_manager,
        transitions=item_transitions,
        stats=app_stats,
    )
    sync_loop = QBitSyncLoop(
        qbit_client=qbit,
        store=store,
        bus=bus,
        task_manager=bg_manager,
        transitions=item_transitions,
    )
    clipboard_monitor = ClipboardMonitor(
        bus=bus,
        store=store,
        classifier=classifier,
        pipeline=pipeline,
        transitions=item_transitions,
    )

    ctx = AppContext(
        store=store,
        bus=bus,
        pipeline=pipeline,
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        api_key=settings.API_KEY,
        stats=app_stats,
        bg_manager=bg_manager,
        broadcaster=broadcaster,
        action_executor=action_executor,
        qbit_sync=sync_loop,
        qbit_lock=qbit_lock,
        clipboard_monitor=clipboard_monitor,
        error_handler=error_handler,
        item_transitions=item_transitions,
    )
    ctx.qbit_runtime = QBitRuntime(
        ctx=ctx,
        settings=settings,
        client_factory=QBittorrentClient,
    )
    return AppRuntime(ctx=ctx, sync_loop=sync_loop)
