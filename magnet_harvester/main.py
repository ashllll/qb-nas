"""
Magnet Harvester v3.0 — 主服务（架构重构版）

变更：
- 依赖注入：所有服务实例在 lifespan 中创建（消除模块级单例）
- ItemStore：替代 found_items 全局字典（有缝可测试）
- MessageBus：替代临时 broadcast dict（事件 fan-out）
- HarvestPipeline：封装 crawl→classify→download（深模块）
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

from magnet_harvester.agent import MagnetAgent
from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.config import settings
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.errors import error_handler, ErrorCategory, ErrorSeverity
from magnet_harvester.models import CrawlRequest, DownloadRequest, TaskStatus
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.store import InMemoryItemStore, ItemStore
from magnet_harvester.tts_client import MinimaxTTS

STATIC_DIR = Path(__file__).parent.parent / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════
# AppContext — 依赖容器
# ═══════════════════════════════════════════════════
@dataclass
class AppContext:
    """所有服务依赖的单一容器。存入 app.state，通过 Depends(get_context) 获取。"""
    store: ItemStore
    bus: MessageBus
    pipeline: HarvestPipeline
    crawler: MagnetCrawler
    classifier: LocalClassifier
    qbit: QBittorrentClient
    tts: MinimaxTTS


def get_context(request: Request) -> AppContext:
    """FastAPI Depends 用 — 从 app.state 获取 AppContext"""
    return request.app.state.ctx

# ═══════════════════════════════════════════════════
# 运行时引用（lifespan 中初始化）
# 替代原来的 found_items dict + 模块级单例
# ═══════════════════════════════════════════════════
_store: InMemoryItemStore | None = None
_bus: MessageBus | None = None
_pipeline: HarvestPipeline | None = None
_crawler: MagnetCrawler | None = None
_classifier: LocalClassifier | None = None
_qbit: QBittorrentClient | None = None
_tts: MinimaxTTS | None = None
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


# ═══════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════
def _bg(coro, name: str | None = None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task) -> None:
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            log.error(f"后台任务 [{task.get_name()}] 异常: {exc}", exc_info=exc)


# ═══════════════════════════════════════════════════
# MessageBus → WebSocket 广播适配器
# ═══════════════════════════════════════════════════
async def _ws_broadcast(event: Event):
    """订阅全部事件，转发到 WebSocket 客户端"""
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


# ═══════════════════════════════════════════════════
# MessageBus → TTS 通知适配器
# ═══════════════════════════════════════════════════
async def _tts_on_event(event: Event):
    if _tts is None:
        return
    if event.type == EventType.CRAWL_DONE:
        await _tts.notify("crawl_done", total=event.data.get("total", 0))
    elif event.type == EventType.DOWNLOAD_DONE:
        await _tts.notify("download_done", count=event.data.get("count", 0))


# ═══════════════════════════════════════════════════
# Agent 工具执行器（通过 ItemStore / Pipeline）
# ═══════════════════════════════════════════════════
async def _tool_executor(name: str, inp: dict) -> dict:
    store = _store
    bus = _bus
    pipeline = _pipeline

    if name == "get_stats":
        s = store.stats()
        return {
            "total": s.total,
            "by_category": s.by_category,
            "by_status": s.by_status,
        }

    if name == "list_items":
        cat = inp.get("category")
        status = inp.get("status", "all")
        limit = int(inp.get("limit", 20))
        items = store.list(category=cat, status=status, limit=limit)
        return {
            "count": len(items),
            "items": [{"hash": i.hash[:16], "name": i.name,
                       "category": i.category, "status": str(i.status)}
                      for i in items],
        }

    if name == "start_crawl":
        url = inp.get("url", "").strip()
        if not url:
            return {"status": "error", "reason": "url 不能为空"}
        depth = int(inp.get("depth", 1))
        _bg(pipeline.execute(url, depth=depth, auto_download=False),
            name=f"crawl:{url[:40]}")
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
        valid = list(settings.CATEGORY_PATHS.keys())
        if cat not in valid:
            return {"status": "error", "reason": f"无效分类，可选: {valid}"}
        matches = store.get_hashes_by_prefix(h)
        if matches:
            match = matches[0]
            store.update(match, category=cat, save_path=settings.CATEGORY_PATHS[cat])
            await bus.emit_nowait(Event(EventType.CLASSIFY_DONE, {
                "hash": match, "category": cat, "confidence": "manual", "reason": "手动修改",
            }))
            return {"status": "ok", "hash": match, "new_category": cat}
        return {"status": "not_found", "hash": h}

    if name == "search_items":
        query = inp.get("query", "")
        hits = store.search(query)
        return {"count": len(hits), "results": [
            {"hash": i.hash[:16], "name": i.name, "category": i.category}
            for i in hits[:20]
        ]}

    if name == "clear_all":
        if not inp.get("confirm"):
            return {"status": "cancelled", "reason": "需要 confirm=true"}
        count = store.count
        store.clear()
        await bus.emit_nowait(Event(EventType.ERROR, {"type": "items_cleared"}))
        return {"status": "cleared", "removed": count}

    return {"error": f"未知工具: {name}"}


# ═══════════════════════════════════════════════════
# FastAPI Lifetime（依赖注入入口）
# ═══════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _bus, _pipeline, _crawler, _classifier, _qbit, _tts

    # ── 创建实例 ──────────────────────────────
    _crawler = MagnetCrawler(config=settings.crawler)
    _qbit = QBittorrentClient(config=settings.qbit)

    # 从 qB 获取默认保存路径作为厂牌基础路径
    adult_base_path = await _qbit.get_default_save_path()
    _classifier = LocalClassifier(adult_base_path=adult_base_path)
    _tts = MinimaxTTS(config=settings.tts)
    _store = InMemoryItemStore()
    _bus = MessageBus()

    _pipeline = HarvestPipeline(
        crawler=_crawler, classifier=_classifier,
        qbit=_qbit, tts=_tts, store=_store, bus=_bus,
    )

    # ── 存入 AppContext ───────────────────────
    app.state.ctx = AppContext(
        store=_store, bus=_bus, pipeline=_pipeline,
        crawler=_crawler, classifier=_classifier,
        qbit=_qbit, tts=_tts,
    )

    # ── 连接适配器 ────────────────────────────
    _bus.subscribe(None, _ws_broadcast)
    _bus.subscribe(None, _tts_on_event)

    # ── 启动 ──────────────────────────────────────
    await _crawler.start()
    disk_info = settings.check_disk_space()
    log.info(
        f"Crawl4AI 已启动 | 本地分类器就绪"
        f"| TTS: {'✅' if settings.tts.enabled else '—'}"
        f"| 磁盘剩余: {disk_info.get('free_gb', 'N/A')}GB"
    )

    yield

    # ── 关闭 ──────────────────────────────────────
    await _crawler.stop()
    await _qbit.close()
    # LocalClassifier 无外部客户端需要关闭
    log.info("服务已关闭")


app = FastAPI(title="Magnet Harvester v3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ═══════════════════════════════════════════════════
# WebSocket 端点
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


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    history: list[dict] = []
    agent = MagnetAgent(
        tool_executor=_tool_executor,
        shared_usage=_classifier.usage if _classifier else None,
        config=settings.classifier,
    )
    log.info("Agent 会话建立")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "msg": "无效的 JSON 格式"}))
                continue

            user_msg = payload.get("message", "").strip()
            if not user_msg:
                continue

            def on_token(token: str):
                asyncio.create_task(
                    ws.send_text(json.dumps({"type": "token", "text": token}, ensure_ascii=False))
                )

            def on_tool_call(name: str, inp: dict):
                asyncio.create_task(
                    ws.send_text(json.dumps({"type": "tool_call", "name": name, "input": inp}, ensure_ascii=False))
                )

            def on_usage(usage_dict: dict):
                asyncio.create_task(
                    ws.send_text(json.dumps({"type": "usage", "data": usage_dict}, ensure_ascii=False))
                )

            try:
                final_text, history = await agent.run(
                    user_msg, history,
                    on_token=on_token, on_tool_call=on_tool_call, on_usage=on_usage,
                )
                await ws.send_text(json.dumps({"type": "done", "text": final_text}, ensure_ascii=False))
            except Exception as e:
                log.error(f"Agent 执行错误: {e}")
                await ws.send_text(json.dumps({"type": "error", "msg": str(e)}, ensure_ascii=False))

    except WebSocketDisconnect:
        log.info("Agent 会话断开")
    finally:
        await agent.close()


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
    disk_info = settings.check_disk_space()
    return {
        "qbittorrent": "online" if qbit_ok else "offline",
        "classifier": "local_rules",
        "tts_enabled": settings.tts.enabled,
        "items_count": _store.count if _store else 0,
        "disk_space": disk_info,
        "qbit_stats": _qbit.get_stats(),
    }


@app.get("/api/stats")
async def get_stats():
    stats.record_api_call()
    return stats.as_dict()


@app.get("/api/usage")
async def get_usage():
    stats.record_api_call()
    return {"total": _classifier.usage.as_dict(), "note": "分类器 + Agent 对话合计用量"}


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
    disk_ok = settings.check_disk_space()["healthy"]
    return {
        "healthy": qbit_ok and disk_ok,
        "qbittorrent": qbit_ok,
        "disk_space": disk_ok,
        "qbit_healthy": _qbit.is_healthy(),
    }


@app.get("/api/disk")
async def get_disk_info():
    stats.record_api_call()
    return {"disk": settings.check_disk_space(), "categories": settings.get_category_stats()}


@app.get("/api/paths/validate")
async def validate_paths():
    results = {}
    for category, path_str in settings.CATEGORY_PATHS.items():
        valid, message = settings.validate_path(path_str)
        results[category] = {"valid": valid, "message": message, "path": path_str}
    return {"results": results}


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
    await _bus.emit_nowait(Event(EventType.ERROR, {"type": "items_cleared"}))
    return {"status": "cleared", "removed": count}


@app.post("/api/cache/clear")
async def clear_cache():
    _classifier.clear_cache()
    return {"status": "cleared"}


@app.get("/api/categories")
async def get_categories():
    return {"categories": list(settings.CATEGORY_PATHS.keys()), "paths": settings.CATEGORY_PATHS}


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("magnet_harvester.main:app", host=settings.SERVICE_HOST, port=settings.SERVICE_PORT, reload=False)
