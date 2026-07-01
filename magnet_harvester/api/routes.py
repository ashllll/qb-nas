"""
REST routes backed by AppContext dependency injection.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from magnet_harvester.config import settings
from magnet_harvester.context.app_context import (
    AppContext,
    ItemQueryLike,
    ObservabilityLike,
    QBitRuntimeLike,
    UserActionExecutorLike,
    get_context,
)
from magnet_harvester.errors import ErrorCategory, ErrorSeverity
from magnet_harvester.models import CrawlRequest, DownloadRequest, QBitConfigUpdate, TaskStatus
from magnet_harvester.utils.auth import require_api_key

log = logging.getLogger(__name__)

VALID_CATEGORIES = {"电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"}

router = APIRouter()


def _actions(ctx: AppContext) -> UserActionExecutorLike:
    if ctx.action_executor is None:
        raise HTTPException(status_code=500, detail="Action executor not configured")
    return ctx.action_executor


def _qbit_runtime(ctx: AppContext) -> QBitRuntimeLike:
    if ctx.qbit_runtime is None:
        raise HTTPException(status_code=500, detail="qBittorrent runtime not configured")
    return ctx.qbit_runtime


def _observability(ctx: AppContext) -> ObservabilityLike:
    if ctx.observability is None:
        raise HTTPException(status_code=500, detail="Observability snapshot not configured")
    return ctx.observability


def _item_queries(ctx: AppContext) -> ItemQueryLike:
    if ctx.item_queries is None:
        raise HTTPException(status_code=500, detail="Item queries not configured")
    return ctx.item_queries


def _task_snapshot(ctx: AppContext, task_id: str) -> dict:
    task_manager = ctx.bg_manager
    get_task = getattr(task_manager, "get_task", None)
    if not callable(get_task):
        raise HTTPException(status_code=500, detail="Background task manager not configured")
    snapshot = get_task(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return snapshot


def _classifier_reload(ctx: AppContext) -> dict:
    reload_rules = getattr(ctx.classifier, "reload_rules", None)
    if reload_rules is None:
        raise HTTPException(status_code=500, detail="Classifier reload not configured")
    return reload_rules()


@router.get("/api/status")
async def system_status(ctx: AppContext = Depends(get_context)):
    return await _observability(ctx).system_status()


@router.get("/api/stats")
async def get_stats(ctx: AppContext = Depends(get_context)):
    return _observability(ctx).api_stats()


@router.get("/api/items")
async def get_items(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: AppContext = Depends(get_context),
):
    if status is not None:
        try:
            TaskStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status: {status}. "
                f"Valid values: {[v.value for v in TaskStatus]}",
            )
    if category is not None and category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category: {category}. Valid values: {sorted(VALID_CATEGORIES)}",
        )
    if ctx.stats is not None:
        ctx.stats.record_api_call()
    return _item_queries(ctx).page_items(
        category=category,
        status=status or "all",
        limit=limit,
        offset=offset,
    )


@router.get("/api/items/search")
async def search_items(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
):
    if ctx.stats is not None:
        ctx.stats.record_api_call()
    return _item_queries(ctx).search_items(query=q, limit=limit)


@router.post("/api/crawl")
async def start_crawl(
    req: CrawlRequest, _=Depends(require_api_key), ctx: AppContext = Depends(get_context)
):
    try:
        result = await _actions(ctx).start_crawl(
            req.url, depth=req.depth, auto_download=req.auto_download
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("start_crawl 异常: %s", exc)
        raise HTTPException(status_code=503, detail="服务暂时不可用")
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result.get("reason", "action failed"))
    return result


@router.get("/api/tasks/{task_id}")
async def get_task_status(
    task_id: str,
    _=Depends(require_api_key),
    ctx: AppContext = Depends(get_context),
):
    return _task_snapshot(ctx, task_id)


@router.post("/api/classifier/reload")
async def reload_classifier(
    ctx: AppContext = Depends(get_context),
    _=Depends(require_api_key),
):
    return _classifier_reload(ctx)


@router.post("/api/download")
async def download_selected(
    req: DownloadRequest, ctx: AppContext = Depends(get_context), _=Depends(require_api_key)
):
    return await _actions(ctx).download(req.hashes)


@router.post("/api/reclassify")
async def reclassify(
    req: DownloadRequest, ctx: AppContext = Depends(get_context), _=Depends(require_api_key)
):
    return await _actions(ctx).reclassify(req.hashes)


@router.get("/api/errors")
async def get_errors(
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    ctx: AppContext = Depends(get_context),
    _=Depends(require_api_key),
):
    if ctx.stats is not None:
        ctx.stats.record_api_call()
    try:
        cat = ErrorCategory(category) if category else None
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid error category: {category}")
    try:
        sev = ErrorSeverity(severity) if severity else None
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid error severity: {severity}")
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
    return await _observability(ctx).health()


@router.get("/api/config")
async def get_config(_=Depends(require_api_key)):
    return {
        "qbit_host": settings.QBIT_HOST,
        "qbit_username": settings.QBIT_USERNAME,
    }


@router.put("/api/config")
async def update_config(
    data: QBitConfigUpdate,
    _=Depends(require_api_key),
    ctx: AppContext = Depends(get_context),
):
    try:
        return await _qbit_runtime(ctx).replace_qbit_config(
            host=data.qbit_host,
            username=data.qbit_username,
            password=data.qbit_password,
        )
    except ValueError as exc:
        log.error("配置验证失败: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        log.error("qBittorrent 配置持久化失败: %s", exc)
        raise HTTPException(status_code=500, detail="qBittorrent 配置持久化失败") from exc


@router.delete("/api/items")
async def clear_items(ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    return await _actions(ctx).clear_items()


@router.get("/api/categories")
async def get_categories():
    return {
        "categories": sorted(VALID_CATEGORIES)
    }


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
