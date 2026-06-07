"""
测试 /api/config 端点 — 查看和修改 qB 连接配置
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus
from magnet_harvester.main import AppContext, get_context


def _make_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/config")
    async def get_config(ctx=Depends(get_context)):
        return {
            "qbit_host": ctx.qbit_config.host,
            "qbit_username": ctx.qbit_config.username,
        }

    @app.put("/api/config")
    async def update_config(data: dict, ctx=Depends(get_context)):
        ctx.qbit_config.host = data.get("qbit_host", ctx.qbit_config.host)
        ctx.qbit_config.username = data.get("qbit_username", ctx.qbit_config.username)
        return {"status": "ok"}

    # 需要依赖注入
    from fastapi import Depends
    app.state.ctx = _make_context()
    return app


def _make_context():
    from magnet_harvester.crawler import MagnetCrawler
    from magnet_harvester.classifier import LocalClassifier
    from magnet_harvester.qbit_client import QBittorrentClient
    from magnet_harvester.tts_client import MinimaxTTS
    from magnet_harvester.config import CrawlerConfig, QBitConfig, TTSConfig
    from magnet_harvester.pipeline import HarvestPipeline

    store = FakeStore()
    bus = NullBus()
    cfg = CrawlerConfig(headless=True, timeout=5)
    crawler = MagnetCrawler(config=cfg)
    classifier = LocalClassifier()
    qbit = QBittorrentClient(config=settings.qbit)
    tts = MinimaxTTS(config=TTSConfig(enabled=False))
    pipeline = HarvestPipeline(crawler=crawler, classifier=classifier, qbit=qbit, tts=tts, store=store, bus=bus)
    return AppContext(store=store, bus=bus, pipeline=pipeline,
                      crawler=crawler, classifier=classifier, qbit=qbit, tts=tts)


if __name__ == "__main__":
    from magnet_harvester.config import settings
    app = _make_test_app()
    with TestClient(app) as client:
        resp = client.get("/api/config")
        print(f"GET /api/config: {resp.status_code} {resp.json()}")

        resp = client.put("/api/config", json={"qbit_host": "http://new-host:8080"})
        print(f"PUT /api/config: {resp.status_code} {resp.json()}")

        resp = client.get("/api/config")
        print(f"GET /api/config after update: {resp.status_code} {resp.json()}")
        assert resp.json()["qbit_host"] == "http://new-host:8080"

    print("=== Config API test passed! ===")
