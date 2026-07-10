"""Magnet Harvester v3.0 — app entrypoint and lifespan assembly."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from magnet_harvester.assembly import build_runtime
from magnet_harvester.api.pages import STATIC_DIR, router as pages_router
from magnet_harvester.api.routes import router as api_router
from magnet_harvester.api.websocket import router as ws_router
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
    try:
        await runtime.start()
    except Exception:
        log.exception("runtime.start() 失败")
        # 验证核心存储服务是否可用：store 不可用为致命错误，应阻止启动
        try:
            await runtime.ctx.core.store.count()
        except Exception:
            log.critical("核心存储 store 不可用，无法启动")
            raise
        log.warning("runtime.start() 部分失败，继续以降级模式运行")

    # qBittorrent 连接检查（可降级：离线时服务仍可运行）
    qbit_ok = False
    try:
        qbit_ok = await runtime.ctx.qbit.ping()
    except Exception:
        log.warning("qBittorrent 连接检查失败，继续以降级模式运行")

    disk_info = settings.check_disk_space()
    log.info(
        f"Scrapling 已启动 | qB: {'在线' if qbit_ok else '离线'} "
        f"| 本地分类器就绪 | 磁盘: {disk_info.get('free_gb', '?')}GB"
    )

    yield

    await runtime.stop()
    log.info("服务已关闭")


app = FastAPI(title="Magnet Harvester v3.0", lifespan=lifespan)

_configure_cors(app)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(pages_router)
app.include_router(api_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "magnet_harvester.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=False,
    )
