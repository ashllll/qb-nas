"""
Magnet Harvester v2.0 — 主服务
- /ws       : 磁力列表实时推送（WebSocket）
- /ws/chat  : Agent 自然语言指令（WebSocket）
- /api/*    : REST 控制接口
- 健康检查、系统统计、配置管理
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from agent import MagnetAgent
from classifier import classifier
from config import settings
from crawler import crawler
from errors import error_handler, ErrorCategory, ErrorSeverity
from models import CrawlRequest, DownloadRequest, MagnetItem, TaskStatus
from qbit_client import qbit
from tts_client import tts

STATIC_DIR = Path(__file__).parent / "static"

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt= "%H:%M:%S",
)
log = logging.getLogger(__name__)

found_items: Dict[str, MagnetItem] = {}
active_ws:   Set[WebSocket]        = set()
task_queue: List[Dict] = []
_start_time = time.time()


class SystemStats:
    def __init__(self):
        self.crawl_requests = 0
        self.download_requests = 0
        self.api_calls = 0
        self.start_time = time.time()
    
    def record_crawl(self):
        self.crawl_requests += 1
    
    def record_download(self):
        self.download_requests += 1
    
    def record_api_call(self):
        self.api_calls += 1
    
    def as_dict(self) -> dict:
        uptime = time.time() - self.start_time
        return {
            "uptime_sec": round(uptime, 1),
            "uptime_human": self._format_uptime(uptime),
            "crawl_requests": self.crawl_requests,
            "download_requests": self.download_requests,
            "api_calls": self.api_calls,
            "active_items": len(found_items),
            "websocket_clients": len(active_ws),
            "error_stats": error_handler.get_error_stats(),
        }
    
    def _format_uptime(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"


stats = SystemStats()


# ── 工具函数 ──────────────────────────────────────────────
def _bg(coro, name: str | None = None) -> asyncio.Task:
    """
    Fire-and-forget create_task，自动挂载异常日志回调。
    Fix B: 裸 create_task 在任务抛异常时完全静默；
           done_callback 确保异常出现在日志中。
    """
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task) -> None:
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            log.error(f"后台任务 [{task.get_name()}] 异常: {exc}", exc_info=exc)


# ── 广播 ──────────────────────────────────────────────────
async def broadcast(msg: dict):
    if not active_ws:
        return
    data = json.dumps(msg, ensure_ascii=False)
    dead = set()
    for ws in active_ws:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    active_ws.difference_update(dead)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await crawler.start()
    ok = await classifier.ping()
    
    disk_info = settings.check_disk_space()
    log.info(
        f"Playwright 已启动 | MiniMax: {'✅' if ok else '❌'} "
        f"| TTS: {'✅' if settings.TTS_ENABLED else '—'}"
        f"| 磁盘剩余: {disk_info.get('free_gb', 'N/A')}GB"
    )
    
    yield
    
    await crawler.stop()
    await qbit.close()
    if classifier._client:
        await classifier._client.close()
    
    log.info("服务已关闭")


app = FastAPI(title="Magnet Harvester", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Agent 工具执行器（定义在 ws_chat 之前，避免前向引用） ─
async def _tool_executor(name: str, inp: dict) -> dict:

    if name == "get_stats":
        return {
            "total":       len(found_items),
            "by_category": dict(Counter(i.category or "未分类" for i in found_items.values())),
            "by_status":   dict(Counter(str(i.status) for i in found_items.values())),
        }

    if name == "list_items":
        cat    = inp.get("category")
        status = inp.get("status", "all")
        limit  = int(inp.get("limit", 20))
        items  = list(found_items.values())
        if cat:
            items = [i for i in items if i.category == cat]
        if status != "all":
            items = [i for i in items if str(i.status) == status]
        return {
            "count": len(items),
            "items": [{"hash": i.hash[:16], "name": i.name,
                       "category": i.category, "status": str(i.status)}
                      for i in items[:limit]],
        }

    if name == "start_crawl":
        url = inp.get("url", "").strip()
        if not url:
            return {"status": "error", "reason": "url 不能为空"}
        depth = int(inp.get("depth", 1))
        _bg(_crawl_task(CrawlRequest(url=url, depth=depth, auto_download=False)),
            name=f"crawl:{url[:40]}")
        return {"status": "started", "url": url, "depth": depth}

    if name == "add_to_queue":
        hashes = inp.get("hashes", [])
        if hashes == ["all"]:
            hashes = [h for h, i in found_items.items() if i.status == TaskStatus.pending]
        _bg(_download_items(hashes), name="download_batch")
        return {"status": "started", "count": len(hashes)}

    if name == "reclassify_item":
        h   = inp.get("hash", "")
        cat = inp.get("category", "")
        if len(h) < 8:
            return {"status": "error", "reason": "hash 至少需要 8 位前缀"}
        valid = list(settings.CATEGORY_PATHS.keys())
        if cat not in valid:
            return {"status": "error", "reason": f"无效分类，可选: {valid}"}
        match = next((k for k in found_items if k.startswith(h)), None)
        if match:
            found_items[match].category  = cat
            found_items[match].save_path = settings.CATEGORY_PATHS[cat]
            await broadcast({"type": "classify_done", "hash": match, "category": cat,
                              "confidence": "manual", "reason": "手动修改"})
            return {"status": "ok", "hash": match, "new_category": cat}
        return {"status": "not_found", "hash": h}

    if name == "search_items":
        query = inp.get("query", "").lower()
        hits  = [{"hash": i.hash[:16], "name": i.name, "category": i.category}
                 for i in found_items.values() if query in i.name.lower()]
        return {"count": len(hits), "results": hits[:20]}

    if name == "clear_all":
        if not inp.get("confirm"):
            return {"status": "cancelled", "reason": "需要 confirm=true"}
        count = len(found_items)
        found_items.clear()
        await broadcast({"type": "items_cleared"})
        return {"status": "cleared", "removed": count}

    return {"error": f"未知工具: {name}"}


# ── 主 WebSocket（磁力列表推送）──────────────────────────
@app.websocket("/ws")
async def ws_main(ws: WebSocket):
    await ws.accept()
    active_ws.add(ws)
    try:
        await ws.send_text(json.dumps(
            {"type": "init", "items": [i.model_dump() for i in found_items.values()]},
            ensure_ascii=False,
        ))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        active_ws.discard(ws)


# ── Agent WebSocket（自然语言指令）──────────────────────
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    history: list[dict] = []
    agent = MagnetAgent(tool_executor=_tool_executor, shared_usage=classifier.usage)
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
                    ws.send_text(json.dumps({"type": "token", "text": token},
                                            ensure_ascii=False))
                )

            def on_tool_call(name: str, inp: dict):
                asyncio.create_task(
                    ws.send_text(json.dumps({"type": "tool_call", "name": name, "input": inp},
                                            ensure_ascii=False))
                )

            def on_usage(usage_dict: dict):
                asyncio.create_task(
                    ws.send_text(json.dumps({"type": "usage", "data": usage_dict},
                                            ensure_ascii=False))
                )

            try:
                final_text, history = await agent.run(
                    user_msg, history,
                    on_token=on_token, on_tool_call=on_tool_call, on_usage=on_usage,
                )
                await ws.send_text(json.dumps({"type": "done", "text": final_text},
                                              ensure_ascii=False))
            except Exception as e:
                log.error(f"Agent 执行错误: {e}")
                await ws.send_text(json.dumps({"type": "error", "msg": str(e)},
                                              ensure_ascii=False))

    except WebSocketDisconnect:
        log.info("Agent 会话断开")
    finally:
        await agent.close()


# ── 爬取流水线 ────────────────────────────────────────────
@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    _bg(_crawl_task(req), name=f"crawl:{req.url[:40]}")
    return {"status": "started", "url": req.url}


async def _crawl_task(req: CrawlRequest):
    await broadcast({"type": "crawl_start", "url": req.url})
    new_items: list[MagnetItem] = []

    async for msg in crawler.crawl(req.url, depth=req.depth):
        t = msg["type"]
        if t == "found":
            item = MagnetItem(**msg["item"])
            if item.hash not in found_items:
                found_items[item.hash] = item
                new_items.append(item)
                await broadcast({"type": "magnet_found", "item": item.model_dump()})
        elif t in ("progress", "error"):
            await broadcast(msg)
        elif t == "done":
            total = msg["total"]
            await broadcast({"type": "crawl_done", "total": total, "url": msg["url"]})
            await tts.notify("crawl_done", total=total)

    if new_items:
        await _stream_classify(new_items)
    if req.auto_download and new_items:
        await _download_items([i.hash for i in new_items])


async def _stream_classify(items: list[MagnetItem]):
    if not items:
        return
    index_to_hash  = {i: item.hash for i, item in enumerate(items)}
    classify_items = [{"index": i, "name": item.name} for i, item in enumerate(items)]
    await broadcast({"type": "classify_start", "count": len(items)})

    def on_result(index: int, result: dict):
        h = index_to_hash.get(index)
        if h and h in found_items:
            found_items[h].category  = result["category"]
            found_items[h].save_path = result["save_path"]
            found_items[h].status    = TaskStatus.pending
            asyncio.create_task(broadcast({
                "type":       "classify_done",
                "hash":       h,
                "category":   result["category"],
                "confidence": result.get("confidence", ""),
                "reason":     result.get("reason", ""),
            }))

    await classifier.classify_stream_batch(classify_items, on_result=on_result)
    await broadcast({"type": "usage_update", "data": classifier.usage.as_dict()})
    await broadcast({"type": "classify_all_done"})


async def _download_items(hashes: list[str]):
    success = 0
    for h in hashes:
        item = found_items.get(h)
        if not item or not item.category:
            continue
        item.status = TaskStatus.adding
        await broadcast({"type": "download_start", "hash": h, "name": item.name})
        try:
            ok = await qbit.add_magnet(item.magnet, item.category, item.save_path or "")
            item.status = TaskStatus.success if ok else TaskStatus.error
            if ok:
                success += 1
        except Exception as e:
            item.status    = TaskStatus.error
            item.error_msg = str(e)
        await broadcast({"type": "download_result", "hash": h, "status": str(item.status)})

    if success:
        await tts.notify("download_done", count=success)


# ── REST API ──────────────────────────────────────────────
@app.post("/api/download")
async def download_selected(req: DownloadRequest):
    _bg(_download_items(req.hashes), name="download_selected")
    return {"status": "started", "count": len(req.hashes)}


@app.post("/api/reclassify")
async def reclassify(req: DownloadRequest):
    targets = [found_items[h] for h in req.hashes if h in found_items]
    _bg(_stream_classify(targets), name="reclassify")
    return {"status": "started"}


@app.get("/api/status")
async def system_status():
    qbit_ok = await qbit.ping()
    disk_info = settings.check_disk_space()
    
    return {
        "qbittorrent": "online" if qbit_ok else "offline",
        "minimax": "online" if classifier._ok else ("checking" if classifier._ok is None else "offline"),
        "minimax_model": settings.MINIMAX_MODEL,
        "thinking_model": settings.MINIMAX_THINKING_MODEL,
        "thinking_recheck": settings.THINKING_RECHECK,
        "tts_enabled": settings.TTS_ENABLED,
        "items_count": len(found_items),
        "disk_space": disk_info,
        "qbit_stats": qbit.get_stats(),
        "classifier_cache": classifier.get_cache_stats(),
    }


@app.get("/api/stats")
async def get_stats():
    stats.record_api_call()
    return stats.as_dict()


@app.get("/api/usage")
async def get_usage():
    stats.record_api_call()
    return {
        "total": classifier.usage.as_dict(),
        "note": "分类器 + Agent 对话合计用量",
    }


@app.get("/api/errors")
async def get_errors(
    category: Optional[str] = Query(None, description="错误类别过滤"),
    severity: Optional[str] = Query(None, description="错误级别过滤"),
    limit: int = Query(50, ge=1, le=200),
):
    stats.record_api_call()
    
    cat = ErrorCategory(category) if category else None
    sev = ErrorSeverity(severity) if severity else None
    
    errors = error_handler.get_recent_errors(cat, sev, limit)
    return {
        "errors": [e.to_dict() for e in errors],
        "stats": error_handler.get_error_stats(),
    }


@app.post("/api/errors/clear")
async def clear_resolved_errors():
    error_handler.clear_resolved()
    return {"status": "cleared"}


@app.get("/api/health")
async def health_check():
    qbit_ok = await qbit.ping()
    minimax_ok = await classifier.ping()
    disk_ok = settings.check_disk_space()["healthy"]
    
    is_healthy = qbit_ok and minimax_ok and disk_ok
    
    return {
        "healthy": is_healthy,
        "qbittorrent": qbit_ok,
        "minimax": minimax_ok,
        "disk_space": disk_ok,
        "qbit_healthy": qbit.is_healthy(),
    }


@app.get("/api/disk")
async def get_disk_info():
    stats.record_api_call()
    disk_info = settings.check_disk_space()
    category_stats = settings.get_category_stats()
    
    return {
        "disk": disk_info,
        "categories": category_stats,
    }


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
    items = list(found_items.values())
    
    if category:
        items = [i for i in items if i.category == category]
    if status:
        items = [i for i in items if str(i.status) == status]
    
    total = len(items)
    items = items[offset:offset + limit]
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [i.model_dump() for i in items],
    }


@app.get("/api/items/search")
async def search_items(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
):
    stats.record_api_call()
    query = q.lower()
    
    hits = [
        {"hash": i.hash[:16], "name": i.name, "category": i.category, "status": str(i.status)}
        for i in found_items.values()
        if query in i.name.lower()
    ][:limit]
    
    return {"count": len(hits), "results": hits}


@app.delete("/api/items")
async def clear_items():
    count = len(found_items)
    found_items.clear()
    await broadcast({"type": "items_cleared"})
    return {"status": "cleared", "removed": count}


@app.post("/api/cache/clear")
async def clear_cache():
    classifier.clear_cache()
    return {"status": "cleared"}


@app.get("/api/categories")
async def get_categories():
    return {
        "categories": list(settings.CATEGORY_PATHS.keys()),
        "paths": settings.CATEGORY_PATHS,
    }


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=False,
    )
