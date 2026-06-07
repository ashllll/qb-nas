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
from magnet_harvester.models import CrawlRequest, DownloadRequest
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.store import InMemoryItemStore, ItemStore

STATIC_DIR = Path(__file__).parent.parent / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════
# AppContext
# ═══════════════════════════════════════════════════
@dataclass
class AppContext:
    store: ItemStore
    bus: MessageBus
    pipeline: HarvestPipeline
    crawler: MagnetCrawler
    classifier: LocalClassifier
    qbit: QBittorrentClient


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


# ── 全局引用 ────────────────────────────
_store: InMemoryItemStore | None = None
_bus: MessageBus | None = None
_pipeline: HarvestPipeline | None = None
_crawler: MagnetCrawler | None = None
_classifier: LocalClassifier | None = None
_qbit: QBittorrentClient | None = None
_active_ws: set[WebSocket] = set()


# ═══════════════════════════════════════════════════
# SystemStats
# ═══════════════════════════════════════════════════
class SystemStats:
    def __init__(self):
        self.crawl_requests = 0
        self.download_requests = 0
        self.api_calls = 0
        self.start_time = time.time()

    def record_crawl(self):    self.crawl_requests += 1
    def record_download(self): self.download_requests += 1
    def record_api_call(self): self.api_calls += 1

    def as_dict(self) -> dict:
        uptime = time.time() - self.start_time
        return {
            "uptime_sec": round(uptime, 1),
            "uptime_human": _format_uptime(uptime),
            "crawl_requests": self.crawl_requests,
            "download_requests": self.download_requests,
            "api_calls": self.api_calls,
            "active_items": _store.count if _store else 0,
            "websocket_clients": len(_active_ws),
            "error_stats": error_handler.get_error_stats(),
        }


stats = SystemStats()


def _format_uptime(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:   return f"{hours}h {minutes}m {secs}s"
    if minutes > 0: return f"{minutes}m {secs}s"
    return f"{secs}s"


# ── 后台任务工具 ──────────────────────────
def _bg(coro, name: str | None = None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task) -> None:
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            log.error(f"后台任务 [{task.get_name()}] 异常: {exc}", exc_info=exc)


# ── WebSocket 广播 ──────────────────────────
async def _ws_broadcast(event: Event):
    if not _active_ws:
        return
    data = json.dumps(event.as_dict(), ensure_ascii=False)
    dead = set()
    for ws in _active_ws:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    _active_ws.difference_update(dead)


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
        return {"count": len(items), "items": [
            {"hash": i.hash[:16], "name": i.name, "category": i.category, "status": str(i.status)}
            for i in items]}

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
        return {"count": len(hits), "results": [
            {"hash": i.hash[:16], "name": i.name, "category": i.category}
            for i in hits[:20]]}

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
    global _store, _bus, _pipeline, _crawler, _classifier, _qbit

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

    _bus.subscribe(None, _ws_broadcast)

    await _crawler.start()
    qbit_ok = await _qbit.ping()
    disk_info = settings.check_disk_space() if hasattr(settings, 'check_disk_space') else {}
    log.info(
        f"Crawl4AI 已启动 | qB: {'在线' if qbit_ok else '离线'} "
        f"| 本地分类器就绪 | 磁盘: {disk_info.get('free_gb', '?')}GB"
    )

    yield

    await _crawler.stop()
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
    await ws.accept()
    _active_ws.add(ws)
    try:
        if _store:
            items = [i.model_dump() for i in _store.list(limit=10000)]
            await ws.send_text(json.dumps({"type": "init", "items": items}, ensure_ascii=False))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _active_ws.discard(ws)


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
    return {
        "qbittorrent": "online" if qbit_ok else "offline",
        "classifier": "local_rules",
        "items_count": _store.count if _store else 0,
        "disk_space": disk_info,
    }


@app.get("/api/stats")
async def get_stats():
    stats.record_api_call()
    return stats.as_dict()


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

    # 重建 qB 客户端
    global _qbit
    _qbit = QBittorrentClient(config=settings.qbit)
    if _pipeline:
        _pipeline._qbit = _qbit
    if hasattr(app.state, 'ctx') and app.state.ctx:
        app.state.ctx.qbit = _qbit

    ok = await _qbit.ping()
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
        "items": [i.model_dump() for i in items[offset:offset + limit]],
    }


@app.get("/api/items/search")
async def search_items(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    stats.record_api_call()
    hits = _store.search(q)
    return {"count": len(hits), "results": [
        {"hash": i.hash[:16], "name": i.name, "category": i.category, "status": str(i.status)}
        for i in hits[:limit]
    ]}


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
