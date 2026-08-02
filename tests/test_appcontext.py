"""
测试 AppContext — 验证依赖可容器化注入
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import FastAPI, Depends

from tests._client import asgi_client

from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus
from magnet_harvester.models import MagnetItem
from magnet_harvester.context.app_context import AppContext, CoreServices, QBitReplacementTarget, RuntimeContext, get_context


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
    pipeline = HarvestPipeline(
        crawler=crawler, classifier=classifier, qbit=qbit, store=store, bus=bus
    )
    return AppContext(
        core=CoreServices(
            store=store, bus=bus, pipeline=pipeline, crawler=crawler, classifier=classifier, qbit=qbit
        ),
    )


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

    with asgi_client(app) as client:
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

    with asgi_client(app) as client:
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
    runtime = RuntimeContext(replacement_target=QBitReplacementTarget.from_context(ctx))

    asyncio.run(runtime.replace_qbit(new_qbit))

    assert ctx.qbit is new_qbit
    assert ctx.pipeline.replaced_with is new_qbit
    assert old_qbit._client is None


@pytest.mark.asyncio
async def test_main_lifespan_populates_runtime_services(monkeypatch):
    import magnet_harvester.main as main_module
    import magnet_harvester.assembly as assembly_module

    class FakeCrawler:
        def __init__(self, config, site_auth=None, task_manager=None):
            self.started = False
            self.stopped = False
            self.max_depth = 3
            self.site_auth = site_auth

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
        def __init__(
            self,
            qbit_client,
            store,
            bus,
            task_manager=None,
            transitions=None,
            poll_interval=2.0,
        ):
            self.started = False
            self.stopped = False
            self.task_manager = task_manager
            self.transitions = transitions
            self.poll_interval = poll_interval

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    class FakeBroadcaster:
        def __init__(self, bus, store=None):
            self.bus = bus
            self.store = store
            self.active_count = 0

    monkeypatch.setattr(assembly_module, "MagnetCrawler", FakeCrawler)
    monkeypatch.setattr(assembly_module, "QBittorrentClient", FakeQbit)
    monkeypatch.setattr(assembly_module, "LocalClassifier", FakeClassifier)
    monkeypatch.setattr(assembly_module, "QBitSyncLoop", FakeSyncLoop)
    monkeypatch.setattr(assembly_module, "WSBroadcaster", FakeBroadcaster)

    test_app = FastAPI(lifespan=main_module.lifespan)

    async with main_module.lifespan(test_app):
        ctx = test_app.state.ctx
        assert ctx.action_executor is not None
        assert ctx.broadcaster is not None
        assert ctx.bg_manager is not None
        assert ctx.stats is not None
        assert ctx.qbit_lock is not None


@pytest.mark.asyncio
async def test_main_lifespan_supports_end_to_end_pipeline_flow(monkeypatch):
    import magnet_harvester.main as main_module
    import magnet_harvester.assembly as assembly_module

    created = {}

    class FakeCrawler:
        def __init__(self, config, site_auth=None, task_manager=None):
            self.started = False
            self.stopped = False
            self.max_depth = 3
            self.site_auth = site_auth
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
        def __init__(
            self,
            qbit_client,
            store,
            bus,
            task_manager=None,
            transitions=None,
            poll_interval=2.0,
        ):
            self.started = False
            self.stopped = False
            self.task_manager = task_manager
            self.transitions = transitions
            self.poll_interval = poll_interval
            created["sync_loop"] = self

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(assembly_module, "MagnetCrawler", FakeCrawler)
    monkeypatch.setattr(assembly_module, "LocalClassifier", FakeClassifier)
    monkeypatch.setattr(assembly_module, "QBittorrentClient", FakeQbit)
    monkeypatch.setattr(assembly_module, "QBitSyncLoop", FakeSyncLoop)

    test_app = FastAPI(lifespan=main_module.lifespan)

    async with main_module.lifespan(test_app):
        ctx = test_app.state.ctx
        await ctx.pipeline.execute("https://example.com", depth=1, auto_download=True)

        assert ctx.store.count == 1
        item = ctx.store.get("ABCDEF1234567890")
        assert item is not None
        assert item.category == "电影"
        assert item.status.value == "queued"
        assert created["qbit"].added == [("magnet:?xt=urn:btih:ABCDEF1234567890", "电影", "电影")]

    assert created["crawler"].started is True
    assert created["crawler"].stopped is True
    assert created["sync_loop"].started is True
    assert created["sync_loop"].stopped is True
    assert created["sync_loop"].poll_interval == assembly_module.settings.QBIT_SYNC_INTERVAL
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
    from magnet_harvester.context.app_context import RuntimeState, AppServices

    # 运行时状态字段在 RuntimeState 上
    runtime_hints = RuntimeState.__annotations__
    for field_name in ("stats", "bg_manager", "qbit_lock"):
        assert "Any" not in str(runtime_hints[field_name]), f"RuntimeState.{field_name}"

    # 用户面向服务字段在 AppServices 上
    app_hints = AppServices.__annotations__
    for field_name in ("broadcaster", "action_executor"):
        assert "Any" not in str(app_hints[field_name]), f"AppServices.{field_name}"


def test_runtime_service_constructor_contracts_are_not_typed_as_any():
    from magnet_harvester.bus import Event, Subscriber
    from magnet_harvester.api.websocket import WSBroadcaster
    from magnet_harvester.errors import ErrorRecord
    from magnet_harvester.models import MetricSnapshot
    from magnet_harvester.pipeline import (
        ClassifyPhase,
        DownloadPhase,
        HarvestPipeline,
        MagnetItemTransitions,
    )
    from magnet_harvester.qbit_client import QBittorrentClient
    from magnet_harvester.services.user_actions import UserActionExecutor
    from magnet_harvester.services.qbit_sync import QBitSyncLoop

    event_hints = Event.__annotations__
    error_record_hints = ErrorRecord.__annotations__
    metric_snapshot_hints = MetricSnapshot.__annotations__
    qbit_client_hints = QBittorrentClient.get_maindata.__annotations__
    qbit_props_hints = QBittorrentClient.get_torrent_properties.__annotations__
    qbit_transfer_hints = QBittorrentClient.get_transfer_info.__annotations__
    websocket_hints = WSBroadcaster.__init__.__annotations__
    classify_usage_hints = ClassifyPhase.usage.fget.__annotations__
    download_phase_hints = DownloadPhase.__annotations__
    transitions_hints = MagnetItemTransitions.__init__.__annotations__
    pipeline_hints = HarvestPipeline.__init__.__annotations__
    action_executor_hints = UserActionExecutor.__init__.__annotations__
    qbit_sync_hints = QBitSyncLoop.__init__.__annotations__

    assert "Any" not in str(event_hints["data"]), "Event.data"
    assert "Any" not in str(Subscriber), "Subscriber"
    assert "Any" not in str(error_record_hints["details"]), "ErrorRecord.details"
    assert "Any" not in str(metric_snapshot_hints["values"]), "MetricSnapshot.values"
    assert "Any" not in str(qbit_client_hints["return"]), "QBittorrentClient.get_maindata"
    assert "Any" not in str(qbit_props_hints["return"]), "QBittorrentClient.get_torrent_properties"
    assert "Any" not in str(qbit_transfer_hints["return"]), "QBittorrentClient.get_transfer_info"
    assert "Any" not in str(websocket_hints["store"]), "WSBroadcaster.store"
    assert "Any" not in str(classify_usage_hints["return"]), "ClassifyPhase.usage"
    assert "last_error" in download_phase_hints, "DownloadPhase.last_error"
    assert "Any" not in str(transitions_hints["store"]), "MagnetItemTransitions.store"
    assert "Any" not in str(pipeline_hints["store"]), "HarvestPipeline.store"

    for field_name in ("store", "pipeline", "task_manager"):
        assert "Any" not in str(action_executor_hints[field_name]), (
            f"UserActionExecutor.{field_name}"
        )

    for field_name in ("qbit_client", "store"):
        assert "Any" not in str(qbit_sync_hints[field_name]), f"QBitSyncLoop.{field_name}"


def test_core_modules_do_not_keep_stale_any_imports():
    import magnet_harvester.pipeline as pipeline_module
    import magnet_harvester.qbit_client as qbit_client_module

    assert not hasattr(pipeline_module, "Any"), "pipeline.Any"
    assert not hasattr(qbit_client_module, "Any"), "qbit_client.Any"


if __name__ == "__main__":
    test_appcontext_holds_deps()
    test_appcontext_in_endpoint()
    test_appcontext_in_lifespan()
    test_runtime_context_replaces_qbit_everywhere()
    asyncio.run(test_main_lifespan_populates_runtime_services())  # pragma: no cover
    print("=== AppContext tests passed! ===")
