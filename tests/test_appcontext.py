"""
测试 AppContext — 验证依赖可容器化注入
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus
from magnet_harvester.models import MagnetItem
from magnet_harvester.context.app_context import AppContext, RuntimeContext, get_context


def _make_test_context() -> AppContext:
    """创建最小测试用 AppContext"""
    from magnet_harvester.crawler import MagnetCrawler
    from magnet_harvester.classifier import LocalClassifier
    from magnet_harvester.qbit_client import QBittorrentClient
    from magnet_harvester.config import CrawlerConfig, QBitConfig
    from magnet_harvester.pipeline import HarvestPipeline

    store = FakeStore()
    bus = NullBus()
    cfg = CrawlerConfig(headless=True, timeout=5)
    crawler = MagnetCrawler(config=cfg)
    classifier = LocalClassifier()
    qbit = QBittorrentClient(config=QBitConfig(host="http://localhost:9999"))
    pipeline = HarvestPipeline(crawler=crawler, classifier=classifier, qbit=qbit, store=store, bus=bus)
    return AppContext(store=store, bus=bus, pipeline=pipeline,
                      crawler=crawler, classifier=classifier, qbit=qbit)


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


def test_runtime_context_replaces_qbit_everywhere():
    import asyncio

    class FakeQbit:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakePipeline:
        def __init__(self):
            self.replaced_with = None

        def replace_download_phase(self, new_qbit):
            self.replaced_with = new_qbit

    ctx = _make_test_context()
    old_qbit = ctx.qbit
    new_qbit = FakeQbit()
    ctx.pipeline = FakePipeline()
    runtime = RuntimeContext(ctx=ctx)

    asyncio.run(runtime.replace_qbit(new_qbit))

    assert ctx.qbit is new_qbit
    assert ctx.pipeline.replaced_with is new_qbit
    assert old_qbit._client is None


@pytest.mark.asyncio
async def test_main_lifespan_populates_runtime_services(monkeypatch):
    import magnet_harvester.main as main_module

    class FakeCrawler:
        def __init__(self, config):
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    class FakeQbit:
        def __init__(self, config):
            self.closed = False

        async def ping(self):
            return True

        async def close(self):
            self.closed = True

    class FakeClassifier:
        pass

    class FakeSyncLoop:
        def __init__(self, qbit_client, store, bus):
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    class FakeBroadcaster:
        def __init__(self, bus, store=None):
            self.bus = bus
            self.store = store
            self.active_count = 0

    class FakeToolExecutor:
        def __init__(self, store, pipeline, bus, task_manager=None):
            self.store = store
            self.pipeline = pipeline
            self.bus = bus
            self.task_manager = task_manager

    monkeypatch.setattr(main_module, "MagnetCrawler", FakeCrawler)
    monkeypatch.setattr(main_module, "QBittorrentClient", FakeQbit)
    monkeypatch.setattr(main_module, "LocalClassifier", FakeClassifier)
    monkeypatch.setattr(main_module, "QBitSyncLoop", FakeSyncLoop)
    monkeypatch.setattr(main_module, "WSBroadcaster", FakeBroadcaster)
    monkeypatch.setattr(main_module, "ToolExecutor", FakeToolExecutor, raising=False)

    test_app = FastAPI(lifespan=main_module.lifespan)

    async with main_module.lifespan(test_app):
        ctx = test_app.state.ctx
        assert ctx.tool_executor is not None
        assert ctx.broadcaster is not None
        assert ctx.bg_manager is not None
        assert ctx.stats is not None
        assert ctx.qbit_lock is not None


@pytest.mark.asyncio
async def test_main_lifespan_supports_end_to_end_pipeline_flow(monkeypatch):
    import magnet_harvester.main as main_module

    created = {}

    class FakeCrawler:
        def __init__(self, config):
            self.started = False
            self.stopped = False
            created["crawler"] = self

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

        async def crawl(self, url, depth=1):
            yield {
                "type": "found",
                "item": {
                    "hash": "ABCDEF1234567890",
                    "name": "Example.Movie.2160p",
                    "magnet": "magnet:?xt=urn:btih:ABCDEF1234567890",
                },
            }
            yield {"type": "done", "total": 1, "url": url}

    class FakeClassifier:
        async def classify_stream_batch(self, items, on_result=None):
            if on_result is not None:
                on_result(
                    0,
                    {
                        "category": "电影",
                        "save_path": "电影",
                        "confidence": "high",
                        "reason": "test",
                    },
                )

    class FakeQbit:
        def __init__(self, config):
            self.closed = False
            self.added = []
            created["qbit"] = self

        async def ping(self):
            return True

        async def close(self):
            self.closed = True

        async def add_magnet(self, magnet, category, save_path):
            self.added.append((magnet, category, save_path))
            return True

        def get_stats(self):
            return {}

    class FakeSyncLoop:
        def __init__(self, qbit_client, store, bus):
            self.started = False
            self.stopped = False
            created["sync_loop"] = self

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(main_module, "MagnetCrawler", FakeCrawler)
    monkeypatch.setattr(main_module, "LocalClassifier", FakeClassifier)
    monkeypatch.setattr(main_module, "QBittorrentClient", FakeQbit)
    monkeypatch.setattr(main_module, "QBitSyncLoop", FakeSyncLoop)

    test_app = FastAPI(lifespan=main_module.lifespan)

    async with main_module.lifespan(test_app):
        ctx = test_app.state.ctx
        await ctx.pipeline.execute("https://example.com", depth=1, auto_download=True)

        assert ctx.store.count == 1
        item = ctx.store.get("ABCDEF1234567890")
        assert item is not None
        assert item.category == "电影"
        assert item.status.value == "queued"
        assert created["qbit"].added == [
            ("magnet:?xt=urn:btih:ABCDEF1234567890", "电影", "电影")
        ]

    assert created["crawler"].started is True
    assert created["crawler"].stopped is True
    assert created["sync_loop"].started is True
    assert created["sync_loop"].stopped is True
    assert created["qbit"].closed is True


def test_main_module_does_not_expose_legacy_runtime_globals():
    import magnet_harvester.main as main_module

    legacy_names = {
        "_store",
        "_bus",
        "_pipeline",
        "_crawler",
        "_classifier",
        "_qbit",
        "_qbit_lock",
        "_broadcaster",
        "_bg",
        "_ensure_qbit_lock",
        "_tool_executor",
        "stats",
    }

    for name in legacy_names:
        assert not hasattr(main_module, name), name


def test_appcontext_runtime_service_slots_are_not_typed_as_any():
    hints = AppContext.__annotations__

    for field_name in ("stats", "bg_manager", "broadcaster", "tool_executor", "qbit_lock"):
        assert "Any" not in str(hints[field_name]), field_name


if __name__ == "__main__":
    test_appcontext_holds_deps()
    test_appcontext_in_endpoint()
    test_appcontext_in_lifespan()
    test_runtime_context_replaces_qbit_everywhere()
    asyncio.run(test_main_lifespan_populates_runtime_services())  # pragma: no cover
    print("=== AppContext tests passed! ===")
