"""
REST routes backed by AppContext dependency injection.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from magnet_harvester.bus import Event, EventType
from magnet_harvester.config import settings
from magnet_harvester.context.app_context import AppContext, RuntimeContext, get_context
from magnet_harvester.errors import ErrorCategory, ErrorSeverity
from magnet_harvester.models import CrawlRequest, DownloadRequest, TaskStatus
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.utils.auth import require_api_key
from magnet_harvester.utils.serializers import item_payload, item_summary

router = APIRouter()


@router.get("/api/status")
async def system_status(ctx: AppContext = Depends(get_context)):
    qbit_ok = await ctx.qbit.ping()
    tracked = len([
        item for item in ctx.store.list(limit=10000)
        if item.status in {TaskStatus.adding, TaskStatus.queued, TaskStatus.downloading}
    ])
    return {
        "qbittorrent": "online" if qbit_ok else "offline",
        "classifier": "local_rules",
        "items_count": ctx.store.count,
        "tracked_downloads": tracked,
        "qbit_stats": ctx.qbit.get_stats(),
        "disk_space": {},
    }


@router.get("/api/stats")
async def get_stats(ctx: AppContext = Depends(get_context)):
    if ctx.stats is not None:
        ctx.stats.record_api_call()
        result = ctx.stats.as_dict()
    else:
        result = {"api_calls": 0}
    result["active_items"] = ctx.store.count
    result["websocket_clients"] = ctx.broadcaster.active_count if ctx.broadcaster else 0
    if ctx.error_handler is not None:
        result["error_stats"] = ctx.error_handler.get_error_stats()
    return result


@router.get("/api/items")
async def get_items(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: AppContext = Depends(get_context),
):
    if ctx.stats is not None:
        ctx.stats.record_api_call()
    items = ctx.store.list(category=category, status=status or "all", limit=10000)
    total = len(items)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [item_payload(i) for i in items[offset:offset + limit]],
    }


@router.get("/api/items/search")
async def search_items(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
):
    if ctx.stats is not None:
        ctx.stats.record_api_call()
    hits = ctx.store.search(q)
    return {"count": len(hits), "results": [item_summary(i) for i in hits[:limit]]}


@router.post("/api/crawl")
async def start_crawl(req: CrawlRequest, ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    try:
        await ctx.pipeline.admit_crawl_target(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if ctx.stats is not None:
        ctx.stats.record_crawl()
    ctx.bg_manager.create(
        ctx.pipeline.execute(req.url, depth=req.depth, auto_download=req.auto_download),
        name=f"crawl:{req.url[:40]}",
    )
    return {"status": "started", "url": req.url}


@router.post("/api/download")
async def download_selected(req: DownloadRequest, ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    if ctx.stats is not None:
        ctx.stats.record_download()
    ctx.bg_manager.create(ctx.pipeline.download(req.hashes), name="download_selected")
    return {"status": "started", "count": len(req.hashes)}


@router.post("/api/reclassify")
async def reclassify(req: DownloadRequest, ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    ctx.bg_manager.create(ctx.pipeline.reclassify(req.hashes), name="reclassify")
    return {"status": "started"}


@router.get("/api/errors")
async def get_errors(
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    ctx: AppContext = Depends(get_context),
):
    if ctx.stats is not None:
        ctx.stats.record_api_call()
    cat = ErrorCategory(category) if category else None
    sev = ErrorSeverity(severity) if severity else None
    eh = ctx.error_handler
    if eh is None:
        return {"errors": [], "stats": {}}
    errors = eh.get_recent_errors(cat, sev, limit)
    return {"errors": [e.to_dict() for e in errors], "stats": eh.get_error_stats()}


@router.post("/api/errors/clear")
async def clear_resolved_errors(ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    if ctx.error_handler is not None:
        ctx.error_handler.clear_resolved()
    return {"status": "cleared"}


@router.get("/api/health")
async def health_check(ctx: AppContext = Depends(get_context)):
    qbit_ok = await ctx.qbit.ping()
    return {"healthy": qbit_ok, "qbittorrent": qbit_ok, "classifier": True}


@router.get("/api/config")
async def get_config():
    return {
        "qbit_host": settings.QBIT_HOST,
        "qbit_username": settings.QBIT_USERNAME,
    }


@router.put("/api/config")
async def update_config(data: dict, ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    host = data.get("qbit_host")
    username = data.get("qbit_username")
    password = data.get("qbit_password")

    lock = ctx.qbit_lock or asyncio.Lock()
    async with lock:
        try:
            candidate = settings.build_qbit_config(
                host=host,
                username=username,
                password=password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        new_qbit = QBittorrentClient(config=candidate)
        if not await new_qbit.ping():
            await new_qbit.close()
            return {"status": "failed", "connected": False}

        await RuntimeContext(ctx).replace_qbit(new_qbit)
        settings.commit_qbit_config(candidate)
        return {"status": "ok", "connected": True}


@router.delete("/api/items")
async def clear_items(ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    count = ctx.store.count
    ctx.store.clear()
    await ctx.bus.emit(Event(EventType.ITEMS_CLEARED, {"type": "items_cleared"}))
    return {"status": "cleared", "removed": count}


@router.get("/api/categories")
async def get_categories():
    return {"categories": ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"]}


@router.get("/api/clipboard")
async def clipboard_status(ctx: AppContext = Depends(get_context)):
    monitor = ctx.clipboard_monitor
    if monitor is None:
        return {"running": False, "magnet_count": 0}
    return {"running": monitor.is_running, "magnet_count": monitor.magnet_count}


@router.post("/api/clipboard/start")
async def clipboard_start(ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    monitor = ctx.clipboard_monitor
    if monitor is None:
        raise HTTPException(status_code=501, detail="Clipboard monitor not available")
    await monitor.start()
    return {"running": True}


@router.post("/api/clipboard/stop")
async def clipboard_stop(ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    monitor = ctx.clipboard_monitor
    if monitor is None:
        raise HTTPException(status_code=501, detail="Clipboard monitor not available")
    await monitor.stop()
    return {"running": False}
