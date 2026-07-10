"""
Test api/routes.py — routes use AppContext dependency injection.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI

from tests._client import asgi_client

from magnet_harvester.errors import ErrorCategory, ErrorSeverity, ErrorHandler
from magnet_harvester.api.routes import router
from magnet_harvester.config import QBitConfig
from magnet_harvester.context.app_context import AppContext, AppServices, CoreServices, RuntimeState, QBitRuntime
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import AsyncItemStore, FakeStore
from magnet_harvester.bus import NullBus
from magnet_harvester.services.item_queries import ItemQueryExecutor
from magnet_harvester.services.observability import ObservabilitySnapshot
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions


class FakeStats:
    def __init__(self):
        self.crawl_requests = 0
        self.download_requests = 0
        self.api_calls = 0

    def record_crawl(self):
        self.crawl_requests += 1

    def record_download(self):
        self.download_requests += 1

    def record_api_call(self):
        self.api_calls += 1

    def as_dict(self):
        return {
            "uptime_sec": 0.0,
            "uptime_human": "0s",
            "crawl_requests": self.crawl_requests,
            "download_requests": self.download_requests,
            "api_calls": self.api_calls,
        }


class FakeActionExecutor:
    async def start_crawl(self, url, *, depth=1, auto_download=False):
        return {"status": "started", "url": url}

    async def download(self, hashes, *, task_name=""):
        return {"status": "started", "count": len(hashes)}

    async def download_pending(self):
        return {"status": "started"}

    async def reclassify(self, hashes):
        return {"status": "started"}

    async def manually_reclassify(self, hash_prefix, category):
        return {"status": "ok"}

    async def clear_items(self):
        return {"status": "cleared"}


class FakeBGManager:
    def __init__(self):
        self.calls = []
        self.snapshots = {
            "task-123": {
                "task_id": "task-123",
                "name": "crawl:https://example.com",
                "status": "running",
                "error": None,
            }
        }

    def create(self, coro, name=None):
        self.calls.append(name)
        coro.close()
        return None

    def get_task(self, task_id):
        return self.snapshots.get(task_id)


class FakePipeline:
    def __init__(self):
        self.replaced_qbit = None
        self.crawl_calls = []

    def max_crawl_depth(self):
        return 2

    async def admit_crawl_target(self, url):
        return url

    async def start_crawl(self, url, *, depth=1, auto_download=False):
        try:
            url = await self.admit_crawl_target(url.strip())
        except ValueError as exc:
            return {"status": "error", "reason": str(exc)}
        depth = max(1, min(int(depth), 3, self.max_crawl_depth()))
        await self.execute(url, depth=depth, auto_download=auto_download)
        return {"status": "started", "url": url, "depth": depth}

    async def execute(self, url, depth=1, auto_download=False):
        self.crawl_calls.append((url, depth, auto_download))
        return None

    async def download(self, hashes):
        return None

    async def reclassify(self, hashes):
        return None

    def replace_download_phase(self, new_qbit):
        self.replaced_qbit = new_qbit


class FakeQbit:
    def __init__(self):
        self.ping_ok = True
        self.stats = {"total_added": 0}
        self.closed = False

    async def ping(self):
        return self.ping_ok

    def get_stats(self):
        return self.stats

    async def close(self):
        self.closed = True


class FakeClassifier:
    def __init__(self):
        self.reload_calls = 0

    def reload_rules(self):
        self.reload_calls += 1
        return {"status": "reloaded", "rules_reloaded": 1}

    def get_cache_stats(self):
        return {"cache_size": 0, "hit_rate": 0.0}


def _make_app():
    store = FakeStore()
    store.add(
        MagnetItem(
            hash="ABCDEF1234567890",
            name="Example.Movie.2160p",
            magnet="magnet:?xt=urn:btih:ABCDEF1234567890",
            category="电影",
            status=TaskStatus.pending,
        )
    )
    error_handler = ErrorHandler()
    pipeline = FakePipeline()
    bg_manager = FakeBGManager()
    stats = FakeStats()
    async_store = AsyncItemStore(store)
    bus = NullBus()
    discovery = DiscoveryTransitions(store=async_store, bus=bus)
    classification = ClassificationTransitions(store=async_store, bus=bus)
    action_executor = UserActionExecutor(
        store=async_store,
        pipeline=pipeline,
        task_manager=bg_manager,
        discovery=discovery,
        classification=classification,
        stats=stats,
    )
    qbit = FakeQbit()
    classifier = FakeClassifier()
    observability = ObservabilitySnapshot(
        store=async_store,
        qbit=qbit,
        stats=stats,
        error_handler=error_handler,
        classifier=classifier,
    )
    item_queries = ItemQueryExecutor(store=async_store)

    class RuntimeSettings:
        def build_qbit_config(self, host, username, password):
            if not host or not host.startswith(("http://", "https://")):
                raise ValueError("非法的 qBittorrent 主机地址")
            if not username:
                raise ValueError("用户名不能为空")
            if not password:
                raise ValueError("密码不能为空")
            return QBitConfig(host=host, username=username, password=password, fs_base_path="")

        def persist_qbit_config(self, config, env_path=None):
            return None

        def commit_qbit_config(self, config):
            return None

    class RuntimeQbit(FakeQbit):
        def __init__(self, config):
            super().__init__()
            self.config = config

    ctx = AppContext(
        core=CoreServices(
            store=async_store,
            bus=NullBus(),
            pipeline=pipeline,
            crawler=None,
            classifier=classifier,
            qbit=qbit,
        ),
        app_services=AppServices(
            action_executor=action_executor,
            observability=observability,
            item_queries=item_queries,
        ),
        runtime=RuntimeState(
            stats=stats,
            bg_manager=bg_manager,
            error_handler=error_handler,
        ),
    )
    ctx.runtime.qbit_runtime = QBitRuntime(
        ctx=ctx,
        settings=RuntimeSettings(),
        client_factory=RuntimeQbit,
    )

    app = FastAPI()
    app.state.ctx = ctx
    app.include_router(router)
    return app, ctx


def test_items_route_uses_context_store():
    app, _ctx = _make_app()

    with asgi_client(app) as client:
        resp = client.get("/api/items")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["status"] == "pending"


def test_items_route_requires_assembled_item_queries():
    app, ctx = _make_app()
    ctx.app_services.item_queries = None

    with asgi_client(app) as client:
        resp = client.get("/api/items")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Item queries not configured"


def test_stats_route_uses_context_stats():
    app, ctx = _make_app()

    with asgi_client(app) as client:
        resp = client.get("/api/stats")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["api_calls"] == 1
    assert payload["active_items"] == 1
    assert payload["websocket_clients"] == 0
    assert ctx.runtime.stats.api_calls == 1


def test_status_route_uses_context_qbit():
    app, _ctx = _make_app()

    with asgi_client(app) as client:
        resp = client.get("/api/status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["qbittorrent"] == "online"
    assert payload["items_count"] == 1


def test_status_route_requires_assembled_observability():
    app, ctx = _make_app()
    ctx.app_services.observability = None

    with asgi_client(app) as client:
        resp = client.get("/api/status")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Observability snapshot not configured"


def test_crawl_route_schedules_pipeline_work():
    app, ctx = _make_app()

    with asgi_client(app) as client:
        resp = client.post("/api/crawl", json={"url": "https://example.com", "depth": 2})

    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    assert ctx.runtime.stats.as_dict()["crawl_requests"] == 1
    assert ctx.core.pipeline.crawl_calls == [("https://example.com", 2, False)]


def test_crawl_route_requires_assembled_action_executor():
    app, ctx = _make_app()
    ctx.app_services.action_executor = None

    with asgi_client(app) as client:
        resp = client.post("/api/crawl", json={"url": "https://example.com", "depth": 2})

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Action executor not configured"


def test_task_status_route_uses_background_task_manager():
    app, _ctx = _make_app()

    with asgi_client(app) as client:
        resp = client.get("/api/tasks/task-123")

    assert resp.status_code == 200
    assert resp.json()["task_id"] == "task-123"
    assert resp.json()["status"] == "running"


def test_classifier_reload_route_uses_context_classifier():
    app, ctx = _make_app()

    with asgi_client(app) as client:
        resp = client.post("/api/classifier/reload")

    assert resp.status_code == 200
    assert resp.json() == {"status": "reloaded", "rules_reloaded": 1}
    assert ctx.core.classifier.reload_calls == 1


def test_download_and_reclassify_routes_schedule_work():
    app, ctx = _make_app()

    with asgi_client(app) as client:
        download = client.post("/api/download", json={"hashes": ["ABCDEF1234567890"]})
        reclassify = client.post("/api/reclassify", json={"hashes": ["ABCDEF1234567890"]})

    assert download.status_code == 200
    assert download.json()["count"] == 1
    assert reclassify.status_code == 200
    assert ctx.runtime.stats.as_dict()["download_requests"] == 1
    assert ctx.runtime.bg_manager.calls == ["download_selected", "reclassify"]


def test_search_clear_health_categories_and_config_routes():
    app, _ctx = _make_app()

    with asgi_client(app) as client:
        search = client.get("/api/items/search", params={"q": "Example"})
        health = client.get("/api/health")
        categories = client.get("/api/categories")
        config = client.get("/api/config")
        cleared = client.delete("/api/items")

    assert search.status_code == 200
    assert search.json()["count"] == 1
    assert health.status_code == 200
    assert health.json()["healthy"] is True
    assert categories.status_code == 200
    assert "电影" in categories.json()["categories"]
    assert config.status_code == 200
    assert "qbit_host" in config.json()
    assert cleared.status_code == 200
    assert cleared.json()["removed"] == 1


def test_errors_routes_return_and_clear_all_records():
    app, ctx = _make_app()
    eh = ctx.runtime.error_handler
    assert eh is not None

    error_id = eh.record(
        ErrorCategory.QBIT,
        ErrorSeverity.ERROR,
        "route test error",
        {"source": "test"},
    )
    eh._errors[error_id].resolved = True

    try:
        with asgi_client(app) as client:
            listed = client.get("/api/errors", params={"category": "qbit", "severity": "error"})
            cleared = client.post("/api/errors/clear")
    finally:
        eh._errors.pop(error_id, None)

    assert listed.status_code == 200
    assert listed.json()["errors"][0]["message"] == "route test error"
    assert cleared.status_code == 200
    assert cleared.json()["status"] == "cleared"


def test_update_config_replaces_qbit_client():
    from magnet_harvester.context.app_context import QBitRuntime
    from magnet_harvester.config import QBitConfig

    created = []
    persisted = []
    committed = []

    class CapturingSettings:
        def build_qbit_config(self, host, username, password):
            return QBitConfig(host=host, username=username, password=password, fs_base_path="")

        def persist_qbit_config(self, config, env_path=None):
            persisted.append(config)

        def commit_qbit_config(self, config):
            committed.append(config)

    class NewQbit(FakeQbit):
        def __init__(self, config):
            super().__init__()
            self.config = config
            created.append(self)

    app, ctx = _make_app()
    ctx.runtime.qbit_runtime = QBitRuntime(
        ctx=ctx,
        settings=CapturingSettings(),
        client_factory=NewQbit,
    )
    old_qbit = ctx.core.qbit

    with asgi_client(app) as client:
        resp = client.put(
            "/api/config",
            json={
                "qbit_host": "http://localhost:8080",
                "qbit_username": "tester",
                "qbit_password": "secret",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "connected": True}
    assert ctx.core.qbit is created[0]
    assert ctx.core.pipeline.replaced_qbit is created[0]
    assert old_qbit.closed is True
    assert created[0].closed is False
    assert persisted == [created[0].config]
    assert committed == [created[0].config]


def test_update_config_requires_assembled_qbit_runtime():
    app, ctx = _make_app()
    ctx.runtime.qbit_runtime = None

    with asgi_client(app) as client:
        resp = client.put(
            "/api/config",
            json={
                "qbit_host": "http://localhost:8080",
                "qbit_username": "tester",
                "qbit_password": "secret",
            },
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "qBittorrent runtime not configured"


def test_update_config_keeps_current_client_when_candidate_cannot_connect():
    from magnet_harvester.context.app_context import QBitRuntime
    from magnet_harvester.config import QBitConfig

    created = []
    persisted = []

    class CapturingSettings:
        def build_qbit_config(self, host, username, password):
            return QBitConfig(host=host, username=username, password=password, fs_base_path="")

        def persist_qbit_config(self, config, env_path=None):
            persisted.append(config)

    class OfflineQbit(FakeQbit):
        def __init__(self, config):
            super().__init__()
            self.config = config
            self.ping_ok = False
            created.append(self)

    app, ctx = _make_app()
    ctx.runtime.qbit_runtime = QBitRuntime(
        ctx=ctx,
        settings=CapturingSettings(),
        client_factory=OfflineQbit,
    )
    old_qbit = ctx.core.qbit

    with asgi_client(app) as client:
        resp = client.put(
            "/api/config",
            json={
                "qbit_host": "http://offline.example:8080",
                "qbit_username": "tester",
                "qbit_password": "secret",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "failed", "connected": False}
    assert ctx.core.qbit is old_qbit
    assert old_qbit.closed is False
    assert created[0].closed is True
    assert persisted == []


def test_update_config_returns_500_when_persist_fails():
    from magnet_harvester.context.app_context import QBitRuntime
    from magnet_harvester.config import QBitConfig

    class FailingSettings:
        def build_qbit_config(self, host, username, password):
            return QBitConfig(host=host, username=username, password=password, fs_base_path="")

        def persist_qbit_config(self, config, env_path=None):
            raise OSError("disk full")

        def commit_qbit_config(self, config):
            raise AssertionError("should not commit after persist failure")

    class NewQbit(FakeQbit):
        def __init__(self, config):
            super().__init__()
            self.config = config

    app, ctx = _make_app()
    ctx.runtime.qbit_runtime = QBitRuntime(
        ctx=ctx,
        settings=FailingSettings(),
        client_factory=NewQbit,
    )
    old_qbit = ctx.core.qbit

    with asgi_client(app) as client:
        resp = client.put(
            "/api/config",
            json={
                "qbit_host": "http://localhost:8080",
                "qbit_username": "tester",
                "qbit_password": "secret",
            },
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "qBittorrent 配置持久化失败"
    assert ctx.core.qbit is old_qbit
    assert old_qbit.closed is False


def test_update_config_rejects_invalid_candidate_without_mutating_runtime():
    app, ctx = _make_app()
    old_qbit = ctx.core.qbit

    with asgi_client(app) as client:
        resp = client.put(
            "/api/config",
            json={
                "qbit_host": "invalid-host",
                "qbit_username": "tester",
                "qbit_password": "secret",
            },
        )

    assert resp.status_code == 422
    assert ctx.core.qbit is old_qbit
    assert old_qbit.closed is False
