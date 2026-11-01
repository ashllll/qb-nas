"""
REST routes backed by AppContext dependency injection.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

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
    if ctx.app_services.action_executor is None:
        raise HTTPException(status_code=500, detail="Action executor not configured")
    return ctx.app_services.action_executor


def _qbit_runtime(ctx: AppContext) -> QBitRuntimeLike:
    if ctx.runtime.qbit_runtime is None:
        raise HTTPException(status_code=500, detail="qBittorrent runtime not configured")
    return ctx.runtime.qbit_runtime


def _observability(ctx: AppContext) -> ObservabilityLike:
    if ctx.app_services.observability is None:
        raise HTTPException(status_code=500, detail="Observability snapshot not configured")
    return ctx.app_services.observability


def _item_queries(ctx: AppContext) -> ItemQueryLike:
    if ctx.app_services.item_queries is None:
        raise HTTPException(status_code=500, detail="Item queries not configured")
    return ctx.app_services.item_queries


def _task_snapshot(ctx: AppContext, task_id: str) -> dict:
    task_manager = ctx.runtime.bg_manager
    get_task = getattr(task_manager, "get_task", None)
    if not callable(get_task):
        raise HTTPException(status_code=500, detail="Background task manager not configured")
    snapshot = get_task(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return snapshot


def _classifier_reload(ctx: AppContext) -> dict:
    reload_rules = getattr(ctx.core.classifier, "reload_rules", None)
    if reload_rules is None:
        raise HTTPException(status_code=500, detail="Classifier reload not configured")
    return reload_rules()


@router.get("/api/status")
async def system_status(ctx: AppContext = Depends(get_context)):
    return await _observability(ctx).system_status()


@router.get("/api/stats")
async def get_stats(ctx: AppContext = Depends(get_context)):
    return await _observability(ctx).api_stats()


@router.get("/api/items")
async def get_items(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    order: str = Query("name", pattern="^(name|newest)$"),
    ctx: AppContext = Depends(get_context),
):
    if not status:
        status = "all"
    if not category:
        category = None
    if status != "all":
        try:
            TaskStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status: {status}. Valid values: {[v.value for v in TaskStatus]}",
            )
    if category is not None and category not in VALID_CATEGORIES:
        # StudioRule 会产生开放分类（厂牌名，如 SexArt）；库里真实存在的分类也要能筛。
        in_use = (await ctx.core.store.stats()).by_category
        if category not in in_use:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid category: {category}. Valid values: {sorted(VALID_CATEGORIES)}",
            )
    result = await _item_queries(ctx).page_items(
        category=category,
        status=status,
        limit=limit,
        offset=offset,
        order=order,
    )
    if ctx.runtime.stats is not None:
        ctx.runtime.stats.record_api_call()
    return result


@router.get("/api/items/search")
async def search_items(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
):
    result = await _item_queries(ctx).search_items(query=q, limit=limit)
    if ctx.runtime.stats is not None:
        ctx.runtime.stats.record_api_call()
    return result


@router.post("/api/crawl")
async def start_crawl(
    req: CrawlRequest, _=Depends(require_api_key), ctx: AppContext = Depends(get_context)
):
    """页面上的「采集」入口。

    配了采集服务时，网址走和 Mac 上报同一条路径（建任务、边抓边入库、结果与原因可查），
    避免页面和 Mac 两条链路行为分叉；没配（老装配/测试）时退回原有 pipeline 路径。
    """
    ingest = getattr(ctx.app_services, "ingest", None)
    if ingest is not None:
        outcome = await ingest.ingest_url(
            req.url,
            source="ui",
            depth=req.depth,
        )
        if not outcome.get("ok"):
            raise HTTPException(status_code=422, detail=outcome.get("reason") or "网址不可用")
        if ctx.runtime.stats is not None:
            ctx.runtime.stats.record_crawl()
        return {
            "status": "started",
            "url": req.url,
            "depth": req.depth,
            "job_id": outcome.get("job_id"),
            "task_id": outcome.get("job_id"),
            "reason": outcome.get("reason") or "",
        }
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
    if not isinstance(result, dict):
        log.error("start_crawl 返回类型异常: %s, 值: %s", type(result), result)
        raise HTTPException(status_code=503, detail="服务内部错误")
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
    try:
        result = await _actions(ctx).download(req.hashes)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("download_selected 异常: %s", exc)
        raise HTTPException(status_code=503, detail="服务暂时不可用")
    if not isinstance(result, dict):
        return JSONResponse({"status": "error", "reason": "unexpected response"}, status_code=503)
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result.get("reason", "action failed"))
    return result


@router.post("/api/reclassify")
async def reclassify(
    req: DownloadRequest, ctx: AppContext = Depends(get_context), _=Depends(require_api_key)
):
    try:
        result = await _actions(ctx).reclassify(req.hashes)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("reclassify 异常: %s", exc)
        raise HTTPException(status_code=503, detail="服务暂时不可用")
    if not isinstance(result, dict):
        log.error("reclassify 返回类型异常: %s, 类型: %s", result, type(result))
        raise HTTPException(status_code=503, detail="服务内部错误")
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result.get("reason") or "action failed")
    return result


@router.get("/api/errors")
async def get_errors(
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    ctx: AppContext = Depends(get_context),
    _=Depends(require_api_key),
):
    if not category:
        category = None
    if not severity:
        severity = None
    try:
        cat = ErrorCategory(category) if category else None
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid error category: {category}")
    try:
        sev = ErrorSeverity(severity) if severity else None
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid error severity: {severity}")
    eh = ctx.runtime.error_handler
    if eh is None:
        return {"errors": [], "stats": {}}
    errors = eh.get_recent_errors(cat, sev, limit)
    if ctx.runtime.stats is not None:
        ctx.runtime.stats.record_api_call()
    return {"errors": [e.to_dict() for e in errors], "stats": eh.get_error_stats()}


@router.post("/api/errors/clear")
async def clear_resolved_errors(ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    if ctx.runtime.error_handler is not None:
        ctx.runtime.error_handler.clear_all()
    return {"status": "cleared", "message": "已清除所有错误"}


@router.get("/api/health")
async def health_check(ctx: AppContext = Depends(get_context)):
    return await _observability(ctx).health()


@router.get("/api/config")
async def get_config(_=Depends(require_api_key)):
    return {
        "qbit_host": settings.QBIT_HOST,
        "qbit_username": settings.QBIT_USERNAME,
        # 只返回是否已配置，绝不把密码或密码的明文/掩码返回给浏览器。
        "qbit_password_configured": bool(settings.QBIT_PASSWORD.strip()),
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
    except RuntimeError as exc:
        log.error("qBittorrent 连接失败: %s", exc)
        raise HTTPException(status_code=503, detail=f"qBittorrent 连接失败: {exc}") from exc
    except OSError as exc:
        log.error("qBittorrent 配置持久化失败: %s", exc)
        raise HTTPException(status_code=500, detail="qBittorrent 配置持久化失败") from exc


@router.delete("/api/items")
async def clear_items(ctx: AppContext = Depends(get_context), _=Depends(require_api_key)):
    try:
        return await _actions(ctx).clear_items()
    except Exception:
        log.exception("清空 items 失败")
        raise HTTPException(status_code=503, detail="清空 items 失败")


@router.get("/api/categories")
async def get_categories(ctx: AppContext = Depends(get_context)):
    """固定分类 + 库里实际出现的分类（厂牌等开放分类）。"""
    try:
        in_use = (await ctx.core.store.stats()).by_category
    except Exception:
        in_use = {}
    extra = {name for name in in_use if name and name != "未分类"}
    return {"categories": sorted(VALID_CATEGORIES | extra)}
