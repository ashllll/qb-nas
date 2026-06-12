"""Magnet Harvester v3.0 — app entrypoint and lifespan assembly."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from magnet_harvester.assembly import build_runtime
from magnet_harvester.api.pages import router as pages_router
from magnet_harvester.api.routes import router as api_router
from magnet_harvester.api.websocket import router as ws_router
from magnet_harvester.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_security_posture()
    runtime = build_runtime()
    app.state.ctx = runtime.ctx
    await runtime.start()
    qbit_ok = await runtime.ctx.qbit.ping()
    disk_info = settings.check_disk_space() if hasattr(settings, 'check_disk_space') else {}
    log.info(
        f"Crawl4AI 已启动 | qB: {'在线' if qbit_ok else '离线'} "
        f"| 本地分类器就绪 | 磁盘: {disk_info.get('free_gb', '?')}GB"
    )

    yield

    await runtime.stop()
    log.info("服务已关闭")


app = FastAPI(title="Magnet Harvester v3.0", lifespan=lifespan)

# CORS: empty string = disabled (same-origin only); comma-separated list = allowed origins
_cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(pages_router)
app.include_router(api_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("magnet_harvester.main:app", host=settings.SERVICE_HOST, port=settings.SERVICE_PORT, reload=False)
