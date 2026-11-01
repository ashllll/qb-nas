"""采集中心入口 API（Mac → NAS）。

- ``POST /api/v1/ingest``：Mac 上报一条磁力或网址，幂等（event_id），默认只入库不下载。
- ``GET  /api/v1/health``：菜单栏看在线（不需要 key，不含任何凭据信息）。
- ``GET  /api/v1/status``：最近条目/任务/最后收到时间，给菜单栏和页面看「最近结果」。
- ``GET  /api/v1/jobs``、``GET /api/v1/jobs/{id}``、``POST /api/v1/jobs/{id}/retry``：
  抓取任务列表、单条详情、手动重试。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from magnet_harvester.config import settings
from magnet_harvester.context.app_context import AppContext, get_context
from magnet_harvester.models import IngestRequest
from magnet_harvester.services.probe import CachedProbe
from magnet_harvester.utils.auth import require_api_key

log = logging.getLogger(__name__)

router = APIRouter()

SERVICE_VERSION = "3.1.0"

# qB 离线时单次 ping 会重试到十几秒；探测结果缓存 10 秒、单次最多等 1.5 秒，
# 菜单栏与页面轮询不会因此被拖慢。
_qbit_probe = CachedProbe(ttl=10.0, timeout=1.5)


async def _qbit_ok(ctx: AppContext) -> bool:
    return await _qbit_probe.read(ctx.core.qbit.ping)


async def _qbit_status(ctx: AppContext) -> dict:
    """qB 可用性 + 失败原因。

    只回一个 ``ok: bool`` 时，界面只能写「qB 离线」——用户不知道是地址错、
    密码错还是网络不通。这里把客户端记录的 last_error 一起给出（不含凭据）。
    """
    ok = await _qbit_ok(ctx)
    reason = ""
    if not ok:
        try:
            reason = str(getattr(ctx.core.qbit, "last_error", "") or "")
        except Exception:
            reason = ""
        reason = reason.strip()[:200]
    if not reason:
        # 客户端没留下原因（例如它的 ping 被别的 ping 挡在锁上、直接取消），
        # 用探针自己的观察兜底：至少要说清「这次没问到 qB」，而不是一片空白。
        reason = str(getattr(_qbit_probe, "last_reason", "") or "")
    return {
        "ok": ok,
        "reason": reason or None,
        # 客户端没留下具体原因时，给一句"该查什么"，不编造原因
        "hint": None
        if ok
        else "检查右侧「连接设置」里的地址与账号，以及 NAS 能否访问 qB（默认 8085 端口）。",
    }


def _ingest_service(ctx: AppContext):
    service = getattr(ctx.app_services, "ingest", None)
    if service is None:
        raise HTTPException(status_code=503, detail="采集服务未就绪")
    return service


def _crawler_ready(ctx: AppContext) -> bool:
    crawler = ctx.core.crawler
    for attr in ("is_ready", "ready", "_started"):
        value = getattr(crawler, attr, None)
        if isinstance(value, bool):
            return value
    return crawler is not None


def _store_backend_label() -> str:
    return settings.STORE_BACKEND or "memory"


@router.post("/api/v1/ingest")
async def ingest_event(
    req: IngestRequest,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_api_key),
):
    """接收 Mac 上报的磁力或网址。

    返回 ``{"ok":true,"status":"accepted"|"duplicate"|"rejected","item_id","job_id","reason"}``。
    业务性拒绝（不是磁力、网址不安全）也返回 200，让 Mac 代理能显示原因而不是当成网络故障。
    """
    service = _ingest_service(ctx)
    try:
        result = await service.handle_event(req, source="mac")
    except Exception:
        log.exception("入库失败 kind=%s", req.kind)
        raise HTTPException(status_code=503, detail="入库失败，请查看 NAS 日志")
    return result


@router.get("/api/v1/health")
async def health_v1(ctx: AppContext = Depends(get_context)):
    """给菜单栏看在线；不含凭据，不需要 key。"""
    store_ok = True
    try:
        await ctx.core.store.count()
    except Exception:
        store_ok = False
    return {
        "ok": store_ok,
        "version": SERVICE_VERSION,
        "store": {"backend": _store_backend_label(), "ok": store_ok},
        "qbit": await _qbit_status(ctx),
        "crawler": {"ok": _crawler_ready(ctx)},
    }


@router.get("/api/v1/status")
async def status_v1(
    jobs: int = Query(10, ge=0, le=50),
    ctx: AppContext = Depends(get_context),
):
    """最近结果：条目/任务/上报事件 + qB、爬虫可用性。

    只读开放：页面首屏必须能显示真实状态，不能因为「这个浏览器没钥匙」就
    把 qB/采集/Mac 上报说成故障。写入口（ingest/jobs/retry）仍要 key。
    """
    service = _ingest_service(ctx)
    snapshot = await service.status_snapshot(jobs_limit=jobs)
    snapshot.update(
        {
            "version": SERVICE_VERSION,
            "store": {"backend": _store_backend_label()},
            "qbit": await _qbit_status(ctx),
            "crawler": {"ok": _crawler_ready(ctx)},
            "items_total": await ctx.core.store.count(),
        }
    )
    return snapshot


@router.get("/api/v1/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    ctx: AppContext = Depends(get_context),
    _=Depends(require_api_key),
):
    service = _ingest_service(ctx)
    return {
        "counts": service.job_counts(),
        "jobs": service.list_jobs(limit=limit, status=status),
    }


@router.get("/api/v1/jobs/{job_id}")
async def get_job(
    job_id: str,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_api_key),
):
    service = _ingest_service(ctx)
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/api/v1/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_api_key),
):
    service = _ingest_service(ctx)
    result = await service.retry_job(job_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("reason") or "任务不存在")
    return result
