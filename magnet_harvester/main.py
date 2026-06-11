"""Magnet Harvester v3.0 — app entrypoint and lifespan assembly."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from magnet_harvester.api.pages import router as pages_router
from magnet_harvester.api.routes import router as api_router
from magnet_harvester.api.websocket import WSBroadcaster, router as ws_router
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    qbit_lock = asyncio.Lock()
    crawler = MagnetCrawler(config=settings.crawler)
    qbit = QBittorrentClient(config=settings.qbit)
    classifier = LocalClassifier()
    store = InMemoryItemStore()
    bus = MessageBus()
    pipeline = HarvestPipeline(
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        store=store,
        bus=bus,
    )
    app_stats = SystemStats()
    bg_manager = BGTaskManager()
    broadcaster = WSBroadcaster(bus=bus, store=store)
    sync_loop = QBitSyncLoop(qbit_client=qbit, store=store, bus=bus)
    tool_executor = ToolExecutor(store=store, pipeline=pipeline, bus=bus, task_manager=bg_manager)

    app.state.ctx = AppContext(
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
    await crawler.start()
    await sync_loop.start()
    qbit_ok = await qbit.ping()
    disk_info = settings.check_disk_space() if hasattr(settings, 'check_disk_space') else {}
    log.info(
        f"Crawl4AI 已启动 | qB: {'在线' if qbit_ok else '离线'} "
        f"| 本地分类器就绪 | 磁盘: {disk_info.get('free_gb', '?')}GB"
    )

    yield

    await sync_loop.stop()
    await crawler.stop()
    await qbit.close()
    log.info("服务已关闭")


app = FastAPI(title="Magnet Harvester v3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(pages_router)
app.include_router(api_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("magnet_harvester.main:app", host=settings.SERVICE_HOST, port=settings.SERVICE_PORT, reload=False)
