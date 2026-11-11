"""Magnet Harvester v3.0 — app entrypoint and lifespan assembly."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from magnet_harvester.api.error_contract import install_error_handlers
from magnet_harvester.api.ingest_routes import router as ingest_router
from magnet_harvester.api.pages import STATIC_DIR, router as pages_router
from magnet_harvester.api.routes import router as api_router
from magnet_harvester.api.security import install_security_guard
from magnet_harvester.api.websocket import router as ws_router
from magnet_harvester.assembly import build_runtime
from magnet_harvester.config import settings
from magnet_harvester.logger import configure_logging

configure_logging(
    level=settings.LOG_LEVEL,
    log_file=settings.LOG_FILE or None,
)
log = logging.getLogger(__name__)


def _configure_cors(app: FastAPI) -> None:
    """在应用启动前配置 CORS 中间件。"""
    cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )


# ═══════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_security_posture()

    runtime = build_runtime()
    app.state.ctx = runtime.ctx
    # 启动步骤内部已各自降级（爬虫/qB/续跑失败只退掉对应功能）；走到这里还抛异常，
    # 说明后台同步循环起不来——带着「页面正常但永不同步」的状态跑，比启动失败更难查。
    await runtime.start()

    # qBittorrent 连接检查（可降级：离线时服务仍可运行）。
    # 限时 3 秒：qB 离线时 ping 会内部重试十几秒，启动不该被它拖住。
    qbit_ok = False
    try:
        qbit_ok = await asyncio.wait_for(runtime.ctx.core.qbit.ping(), timeout=3.0)
    except (asyncio.TimeoutError, Exception):
        log.warning("qBittorrent 连接检查失败（降级运行：入库照常，手动下载需 qB 在线）")

    disk_info = settings.check_disk_space()
    log.info(
        f"Scrapling Spider 已就绪 | qB: {'在线' if qbit_ok else '离线'} "
        f"| 本地分类器就绪 | 磁盘: {disk_info.get('free_gb', '?')}GB"
    )

    yield

    await runtime.stop()
    log.info("服务已关闭")


app = FastAPI(title="Magnet Harvester v3.0", lifespan=lifespan)

# 约定第 2 条：非 2xx 一律 {detail, code, hint}（401 要分清缺失/不对）
install_error_handlers(app)
_configure_cors(app)
# Host（DNS rebinding）与跨源写（CSRF）在入口统一挡；详见 api/security.py
install_security_guard(app)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(pages_router)
app.include_router(api_router)
app.include_router(ingest_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn

    from magnet_harvester.logger import uvicorn_log_config

    uvicorn.run(
        "magnet_harvester.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=False,
        log_config=uvicorn_log_config(),
    )
