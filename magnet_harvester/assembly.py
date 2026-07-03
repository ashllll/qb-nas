"""Application runtime assembly helpers.

Splits build_runtime() into focused sub-functions so individual pieces
can be overridden in tests or extended without touching the full wiring.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from magnet_harvester.api.websocket import WSBroadcaster
from magnet_harvester.bus import MessageBus
from magnet_harvester.classifier import LocalClassifier
from magnet_harvester.config import settings
from magnet_harvester.context.app_context import (
    AppContext,
    AppServices,
    CoreServices,
    QBitRuntime,
    RuntimeState,
)
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.errors import error_handler
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.services.clipboard_monitor import ClipboardMonitor
from magnet_harvester.services.item_queries import ItemQueryExecutor
from magnet_harvester.services.observability import ObservabilitySnapshot
from magnet_harvester.services.qbit_sync import QBitSyncLoop
from magnet_harvester.services.site_auth import SiteAuth
from magnet_harvester.services.stats import SystemStats
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.store import InMemoryItemStore, SQLiteItemStore
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)


@dataclass
class AppRuntime:
    ctx: AppContext
    sync_loop: QBitSyncLoop

    async def start(self):
        # 爬虫和同步循环独立启动，互不阻塞
        try:
            await self.ctx.crawler.start()
        except Exception as e:
            log.error("crawler 启动失败（降级模式，爬取功能不可用）: %s", e)
        await self.sync_loop.start()

    async def stop(self):
        try:
            await self.sync_loop.stop()
        except Exception as e:
            log.error("sync_loop 关闭失败: %s", e)

        if self.ctx.bg_manager is not None:
            try:
                await self.ctx.bg_manager.shutdown()
            except Exception as e:
                log.error("bg_manager 关闭失败: %s", e)

        try:
            await self.ctx.crawler.stop()
        except Exception as e:
            log.error("crawler 关闭失败: %s", e)

        try:
            await self.ctx.qbit.close()
        except Exception as e:
            log.error("qbit 关闭失败: %s", e)


# ── Sub-builders ────────────────────────────────


def _build_store():
    """Create the ItemStore backend based on configuration."""
    if settings.STORE_BACKEND == "sqlite":
        store = SQLiteItemStore(db_path=settings.STORE_DB_PATH)
        log.info("使用 SQLite 持久化存储: %s", settings.STORE_DB_PATH)
    else:
        store = InMemoryItemStore()
        log.info("使用内存存储")
    return store


def _build_core():
    """Fundamental singletons: infrastructure layer components."""
    qbit_lock = asyncio.Lock()
    site_auth = SiteAuth.from_raw(settings.SITE_COOKIES)
    crawler = MagnetCrawler(config=settings.crawler, site_auth=site_auth)
    qbit = QBittorrentClient(config=settings.qbit)
    classifier = LocalClassifier()
    store = _build_store()
    bus = MessageBus()
    bg_manager = BGTaskManager()
    return qbit_lock, site_auth, crawler, qbit, classifier, store, bus, bg_manager


def _build_data_layer(store, bus):
    """Data layer: transitions (state machine) and query executor."""
    transitions = MagnetItemTransitions(store=store, bus=bus)
    queries = ItemQueryExecutor(store=store)
    return transitions, queries


def _build_pipeline(crawler, classifier, qbit, store, bus, bg_manager, transitions):
    """Core pipeline: orchestrates crawl → classify → download."""
    pipeline = HarvestPipeline(
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        store=store,
        bus=bus,
        task_manager=bg_manager,
        transitions=transitions,
    )
    return pipeline


def _build_services(store, bus, pipeline, qbit, classifier, bg_manager, transitions, stats):
    """Application services: observability, actions, sync, clipboard."""
    broadcaster = WSBroadcaster(bus=bus, store=store)
    observability = ObservabilitySnapshot(
        store=store,
        qbit=qbit,
        stats=stats,
        broadcaster=broadcaster,
        error_handler=error_handler,
        classifier=classifier,
    )
    action_executor = UserActionExecutor(
        store=store,
        pipeline=pipeline,
        task_manager=bg_manager,
        transitions=transitions,
        stats=stats,
    )
    sync_loop = QBitSyncLoop(
        qbit_client=qbit,
        store=store,
        bus=bus,
        task_manager=bg_manager,
        transitions=transitions,
        poll_interval=settings.QBIT_SYNC_INTERVAL,
    )
    clipboard_monitor = ClipboardMonitor(
        bus=bus,
        store=store,
        classifier=classifier,
        pipeline=pipeline,
        action_executor=action_executor,
        transitions=transitions,
        task_manager=bg_manager,
    )
    return observability, action_executor, sync_loop, clipboard_monitor, broadcaster


# ── Public API ──────────────────────────────────


def build_runtime() -> AppRuntime:
    """Build the full application runtime with all components wired.

    Uses modular sub-builders for readability and testability.
    """
    qbit_lock, site_auth, crawler, qbit, classifier, store, bus, bg_manager = _build_core()
    transitions, queries = _build_data_layer(store, bus)
    pipeline = _build_pipeline(crawler, classifier, qbit, store, bus, bg_manager, transitions)

    stats = SystemStats()
    observability, action_executor, sync_loop, clipboard_monitor, broadcaster = _build_services(
        store=store,
        bus=bus,
        pipeline=pipeline,
        qbit=qbit,
        classifier=classifier,
        bg_manager=bg_manager,
        transitions=transitions,
        stats=stats,
    )

    ctx = AppContext(
        core=CoreServices(
            store=store,
            bus=bus,
            pipeline=pipeline,
            crawler=crawler,
            classifier=classifier,
            qbit=qbit,
        ),
        app_services=AppServices(
            action_executor=action_executor,
            observability=observability,
            item_queries=queries,
            clipboard_monitor=clipboard_monitor,
            broadcaster=broadcaster,
        ),
        runtime=RuntimeState(
            api_key=settings.API_KEY,
            stats=stats,
            bg_manager=bg_manager,
            qbit_lock=qbit_lock,
            error_handler=error_handler,
            item_transitions=transitions,
            qbit_sync=sync_loop,
        ),
    )
    # QBitRuntime 持有 ctx 回引用，与 AppContext 形成循环引用。
    # 这是已知的刻意设计：QBitRuntime 需要访问 AppContext 的运行时组件
    # （如 bg_manager、qbit_lock），且两者生命周期一致（应用启动→关闭），
    # 由 Python GC 正常回收，无需 weakref 介入。
    ctx.qbit_runtime = QBitRuntime(
        ctx=ctx,
        settings=settings,
        client_factory=QBittorrentClient,
    )
    return AppRuntime(ctx=ctx, sync_loop=sync_loop)
