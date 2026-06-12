"""
REST routes backed by AppContext dependency injection.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from magnet_harvester.bus import Event, EventType
from magnet_harvester.config import settings
from magnet_harvester.context.app_context import AppContext, RuntimeContext, get_context
from magnet_harvester.errors import ErrorCategory, ErrorSeverity, error_handler
from magnet_harvester.models import CrawlRequest, DownloadRequest, TaskStatus
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.utils.auth import require_api_key
from magnet_harvester.utils.serializers import _item_payload, _item_summary

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
    result["error_stats"] = error_handler.get_error_stats()
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
        "items": [_item_payload(i) for i in items[offset:offset + limit]],
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
    return {"count": len(hits), "results": [_item_summary(i) for i in hits[:limit]]}


@router.post("/api/crawl")
async def start_crawl(req: CrawlRequest, ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
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
    errors = error_handler.get_recent_errors(cat, sev, limit)
    return {"errors": [e.to_dict() for e in errors], "stats": error_handler.get_error_stats()}


@router.post("/api/errors/clear")
async def clear_resolved_errors(_=Depends(require_api_key)):
    error_handler.clear_resolved()
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

    settings.update_qbit(host=host, username=username, password=password)
    new_qbit = QBittorrentClient(config=settings.qbit)

    if ctx.qbit_lock is not None:
        async with ctx.qbit_lock:
            await RuntimeContext(ctx).replace_qbit(new_qbit)
            ok = await new_qbit.ping()
    else:
        await RuntimeContext(ctx).replace_qbit(new_qbit)
        ok = await new_qbit.ping()

    return {"status": "ok" if ok else "failed", "connected": ok}


@router.delete("/api/items")
async def clear_items(ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    count = ctx.store.count
    ctx.store.clear()
    await ctx.bus.emit(Event(EventType.ERROR, {"type": "items_cleared"}))
    return {"status": "cleared", "removed": count}


@router.get("/api/categories")
async def get_categories():
    return {"categories": ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"]}
