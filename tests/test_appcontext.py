"""
测试 AppContext — 验证依赖可容器化注入
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus
from magnet_harvester.models import MagnetItem
from magnet_harvester.main import AppContext, get_context


def _make_test_context() -> AppContext:
    """创建最小测试用 AppContext"""
    from magnet_harvester.crawler import MagnetCrawler
    from magnet_harvester.classifier import MiniMaxClassifier
    from magnet_harvester.qbit_client import QBittorrentClient
    from magnet_harvester.tts_client import MinimaxTTS
    from magnet_harvester.config import CrawlerConfig, ClassifierConfig, QBitConfig, TTSConfig
    from magnet_harvester.pipeline import HarvestPipeline

    store = FakeStore()
    bus = NullBus()
    cfg = CrawlerConfig(headless=True, timeout=5)
    crawler = MagnetCrawler(config=cfg)
    classifier = MiniMaxClassifier(config=ClassifierConfig(api_key="test"))
    qbit = QBittorrentClient(config=QBitConfig(host="http://localhost:9999"))
    tts = MinimaxTTS(config=TTSConfig(enabled=False))
    pipeline = HarvestPipeline(crawler=crawler, classifier=classifier, qbit=qbit, tts=tts, store=store, bus=bus)
    return AppContext(store=store, bus=bus, pipeline=pipeline,
                      crawler=crawler, classifier=classifier, qbit=qbit, tts=tts)


def test_appcontext_holds_deps():
    ctx = _make_test_context()
    assert ctx.store is not None
    assert ctx.bus is not None
    assert ctx.pipeline is not None


def test_appcontext_in_endpoint():
    ctx = _make_test_context()
    app = FastAPI()

    @app.get("/test/count")
    async def test_count(ctx: AppContext = Depends(get_context)):
        return {"count": ctx.store.count}

    app.state.ctx = ctx

    with TestClient(app) as client:
        resp = client.get("/test/count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

        ctx.store.add(MagnetItem(hash="TEST", name="test", magnet="magnet:?xt=urn:btih:TEST"))
        resp = client.get("/test/count")
        assert resp.json()["count"] == 1


def test_appcontext_in_lifespan():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        app.state.ctx = _make_test_context()
        yield

    app = FastAPI(lifespan=test_lifespan)

    @app.get("/ping")
    async def ping(ctx: AppContext = Depends(get_context)):
        return {"ok": ctx.store.count == 0}

    with TestClient(app) as client:
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


if __name__ == "__main__":
    test_appcontext_holds_deps()
    test_appcontext_in_endpoint()
    test_appcontext_in_lifespan()
    print("=== AppContext tests passed! ===")
