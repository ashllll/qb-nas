"""
Magnet Harvester v3.0 — 主服务
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.classifier import LocalClassifier
from magnet_harvester.config import settings
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.errors import error_handler, ErrorCategory, ErrorSeverity
from magnet_harvester.models import CrawlRequest, DownloadRequest, MagnetItem, TaskStatus
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.store import InMemoryItemStore, ItemStore
from magnet_harvester.context.app_context import AppContext, RuntimeContext, get_context
from magnet_harvester.utils.serializers import _item_summary, _item_payload
from magnet_harvester.utils.bg_tasks import BGTaskManager
from magnet_harvester.services.stats import SystemStats
from magnet_harvester.services.qbit_sync import QBitSyncLoop
from magnet_harvester.api.websocket import WSBroadcaster

STATIC_DIR = Path(__file__).parent.parent / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── 全局引用 ────────────────────────────
_store: InMemoryItemStore | None = None
_bus: MessageBus | None = None
_pipeline: HarvestPipeline | None = None
_crawler: MagnetCrawler | None = None
_classifier: LocalClassifier | None = None
_qbit: QBittorrentClient | None = None
_qbit_lock: asyncio.Lock | None = None
_broadcaster: WSBroadcaster | None = None


stats = SystemStats()


def _ensure_qbit_lock() -> asyncio.Lock:
    global _qbit_lock
    if _qbit_lock is None:
        _qbit_lock = asyncio.Lock()
    return _qbit_lock


# ── 后台任务工具 ──────────────────────────
_bg_manager = BGTaskManager()

def _bg(coro, name: str | None = None) -> asyncio.Task:
    return _bg_manager.create(coro, name=name)


# ── WebSocket 广播 ──────────────────────────
async def _emit_store_changed(hash_key: str, previous_status: TaskStatus | None = None):
    if not _store or not _bus:
        return

    item = _store.get(hash_key)
    if item is None:
        return

    await _bus.emit(Event(EventType.STORE_CHANGED, {"item": _item_payload(item)}))

    if previous_status is not None and previous_status != item.status:
        await _bus.emit(Event(EventType.DOWNLOAD_RESULT, {
            "hash": hash_key,
            "status": item.status.value,
            "error_msg": item.error_msg,
            "progress": item.progress,
            "torrent_state": item.torrent_state,
        }))


# ── Agent 工具执行器（通过 ItemStore / Pipeline）──
async def _tool_executor(name: str, inp: dict) -> dict:
    store = _store
    pipeline = _pipeline

    if name == "get_stats":
        s = store.stats()
        return {"total": s.total, "by_category": s.by_category, "by_status": s.by_status}

    if name == "list_items":
        cat = inp.get("category")
        status = inp.get("status", "all")
        limit = int(inp.get("limit", 20))
        items = store.list(category=cat, status=status, limit=limit)
        return {"count": len(items), "items": [_item_summary(i) for i in items]}

    if name == "start_crawl":
        url = inp.get("url", "").strip()
        if not url:
            return {"status": "error", "reason": "url 不能为空"}
        depth = int(inp.get("depth", 1))
        _bg(pipeline.execute(url, depth=depth, auto_download=False), name=f"crawl:{url[:40]}")
        return {"status": "started", "url": url, "depth": depth}

    if name == "add_to_queue":
        hashes = inp.get("hashes", [])
        if hashes == ["all"]:
            pending = store.get_pending()
            hashes = [i.hash for i in pending]
        _bg(pipeline.download(hashes), name="download_batch")
        return {"status": "started", "count": len(hashes)}

    if name == "reclassify_item":
        h = inp.get("hash", "")
        cat = inp.get("category", "")
        if len(h) < 8:
            return {"status": "error", "reason": "hash 至少需要 8 位前缀"}
        matches = store.get_hashes_by_prefix(h)
        if matches:
            match = matches[0]
            store.update(match, category=cat, save_path=cat)
            await _bus.emit(Event(EventType.CLASSIFY_DONE, {
                "hash": match, "category": cat, "confidence": "manual", "reason": "手动修改"}))
            return {"status": "ok", "hash": match, "new_category": cat}
        return {"status": "not_found", "hash": h}

    if name == "search_items":
        query = inp.get("query", "")
        hits = store.search(query)
        return {"count": len(hits), "results": [_item_summary(i) for i in hits[:20]]}

    if name == "clear_all":
        if not inp.get("confirm"):
            return {"status": "cancelled", "reason": "需要 confirm=true"}
        count = store.count
        store.clear()
        await _bus.emit(Event(EventType.ERROR, {"type": "items_cleared"}))
        return {"status": "cleared", "removed": count}

    return {"error": f"未知工具: {name}"}


# ═══════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _bus, _pipeline, _crawler, _classifier, _qbit, _qbit_lock

    _qbit_lock = asyncio.Lock()
    _crawler = MagnetCrawler(config=settings.crawler)
    _qbit = QBittorrentClient(config=settings.qbit)

    _classifier = LocalClassifier()
    _store = InMemoryItemStore()
    _bus = MessageBus()

    _pipeline = HarvestPipeline(
        crawler=_crawler, classifier=_classifier,
        qbit=_qbit, store=_store, bus=_bus,
    )

    app.state.ctx = AppContext(
        store=_store, bus=_bus, pipeline=_pipeline,
        crawler=_crawler, classifier=_classifier,
        qbit=_qbit,
    )

    global _broadcaster
    _broadcaster = WSBroadcaster(bus=_bus, store=_store)
    sync_loop = QBitSyncLoop(qbit_client=_qbit, store=_store, bus=_bus)

    await _crawler.start()
    await sync_loop.start()
    qbit_ok = await _qbit.ping()
    disk_info = settings.check_disk_space() if hasattr(settings, 'check_disk_space') else {}
    log.info(
        f"Crawl4AI 已启动 | qB: {'在线' if qbit_ok else '离线'} "
        f"| 本地分类器就绪 | 磁盘: {disk_info.get('free_gb', '?')}GB"
    )

    yield

    await sync_loop.stop()
    await _crawler.stop()
    await _qbit.close()
    log.info("服务已关闭")


app = FastAPI(title="Magnet Harvester v3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ═══════════════════════════════════════════════════
# WebSocket
# ═══════════════════════════════════════════════════
@app.websocket("/ws")
async def ws_main(ws: WebSocket):
    if _broadcaster:
        await _broadcaster.handle_connection(ws)


# ═══════════════════════════════════════════════════
# REST API
# ═══════════════════════════════════════════════════

@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    stats.record_crawl()
    _bg(_pipeline.execute(req.url, depth=req.depth, auto_download=req.auto_download),
        name=f"crawl:{req.url[:40]}")
    return {"status": "started", "url": req.url}


@app.post("/api/download")
async def download_selected(req: DownloadRequest):
    stats.record_download()
    _bg(_pipeline.download(req.hashes), name="download_selected")
    return {"status": "started", "count": len(req.hashes)}


@app.post("/api/reclassify")
async def reclassify(req: DownloadRequest):
    _bg(_pipeline.reclassify(req.hashes), name="reclassify")
    return {"status": "started"}


@app.get("/api/status")
async def system_status():
    qbit_ok = await _qbit.ping()
    disk_info = {}
    tracked = 0
    if _store:
        tracked = len([
            item for item in _store.list(limit=10000)
            if item.status in {TaskStatus.adding, TaskStatus.queued, TaskStatus.downloading}
        ])
    return {
        "qbittorrent": "online" if qbit_ok else "offline",
        "classifier": "local_rules",
        "items_count": _store.count if _store else 0,
        "tracked_downloads": tracked,
        "qbit_stats": _qbit.get_stats() if _qbit else {},
        "disk_space": disk_info,
    }


@app.get("/api/stats")
async def get_stats():
    stats.record_api_call()
    result = stats.as_dict()
    result["active_items"] = _store.count if _store else 0
    result["websocket_clients"] = _broadcaster.active_count if _broadcaster else 0
    result["error_stats"] = error_handler.get_error_stats()
    return result


@app.get("/api/errors")
async def get_errors(
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    stats.record_api_call()
    cat = ErrorCategory(category) if category else None
    sev = ErrorSeverity(severity) if severity else None
    errors = error_handler.get_recent_errors(cat, sev, limit)
    return {"errors": [e.to_dict() for e in errors], "stats": error_handler.get_error_stats()}


@app.post("/api/errors/clear")
async def clear_resolved_errors():
    error_handler.clear_resolved()
    return {"status": "cleared"}


@app.get("/api/health")
async def health_check():
    qbit_ok = await _qbit.ping()
    return {"healthy": qbit_ok, "qbittorrent": qbit_ok, "classifier": True}


# ── 配置管理 ──────────────────────────────

@app.get("/api/config")
async def get_config():
    """获取 qBittorrent 连接配置（不返回密码）"""
    return {
        "qbit_host": settings.QBIT_HOST,
        "qbit_username": settings.QBIT_USERNAME,
    }


@app.put("/api/config")
async def update_config(data: dict):
    """更新 qBittorrent 连接配置并重建客户端"""
    host = data.get("qbit_host")
    username = data.get("qbit_username")
    password = data.get("qbit_password")

    settings.update_qbit(host=host, username=username, password=password)

    global _qbit
    new_qbit = QBittorrentClient(config=settings.qbit)
    lock = _ensure_qbit_lock()
    async with lock:
        _qbit = new_qbit
        if hasattr(app.state, 'ctx') and app.state.ctx:
            await RuntimeContext(app.state.ctx).replace_qbit(new_qbit)

        ok = await new_qbit.ping()
    return {"status": "ok" if ok else "failed", "connected": ok}


# ── 项目接口 ──────────────────────────────

@app.get("/api/items")
async def get_items(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    stats.record_api_call()
    items = _store.list(category=category, status=status or "all", limit=10000)
    total = len(items)
    return {
        "total": total, "limit": limit, "offset": offset,
        "items": [_item_payload(i) for i in items[offset:offset + limit]],
    }


@app.get("/api/items/search")
async def search_items(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    stats.record_api_call()
    hits = _store.search(q)
    return {"count": len(hits), "results": [_item_summary(i) for i in hits[:limit]]}


@app.delete("/api/items")
async def clear_items():
    count = _store.count
    _store.clear()
    await _bus.emit(Event(EventType.ERROR, {"type": "items_cleared"}))
    return {"status": "cleared", "removed": count}


@app.get("/api/categories")
async def get_categories():
    return {"categories": ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"]}


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("magnet_harvester.main:app", host=settings.SERVICE_HOST, port=settings.SERVICE_PORT, reload=False)
