"""
Test api/routes.py — routes use AppContext dependency injection.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magnet_harvester.errors import error_handler, ErrorCategory, ErrorSeverity
from magnet_harvester.api.routes import router
from magnet_harvester.context.app_context import AppContext
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus


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


class FakeToolExecutor:
    async def execute(self, name: str, inp: dict) -> dict:
        return {"tool": name, "input": inp}


class FakeBGManager:
    def __init__(self):
        self.calls = []

    def create(self, coro, name=None):
        self.calls.append(name)
        coro.close()
        return None


class FakePipeline:
    def __init__(self):
        self.replaced_qbit = None

    async def admit_crawl_target(self, url):
        return url

    async def execute(self, url, depth=1, auto_download=False):
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
    ctx = AppContext(
        store=store,
        bus=NullBus(),
        pipeline=FakePipeline(),
        crawler=None,
        classifier=None,
        qbit=FakeQbit(),
        stats=FakeStats(),
        bg_manager=FakeBGManager(),
        tool_executor=FakeToolExecutor(),
    )

    app = FastAPI()
    app.state.ctx = ctx
    app.include_router(router)
    return app, ctx


def test_items_route_uses_context_store():
    app, _ctx = _make_app()

    with TestClient(app) as client:
        resp = client.get("/api/items")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["status"] == "pending"


def test_stats_route_uses_context_stats():
    app, ctx = _make_app()

    with TestClient(app) as client:
        resp = client.get("/api/stats")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["api_calls"] == 1
    assert payload["active_items"] == 1
    assert payload["websocket_clients"] == 0
    assert ctx.stats.api_calls == 1


def test_status_route_uses_context_qbit():
    app, _ctx = _make_app()

    with TestClient(app) as client:
        resp = client.get("/api/status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["qbittorrent"] == "online"
    assert payload["items_count"] == 1


def test_crawl_route_schedules_pipeline_work():
    app, ctx = _make_app()

    with TestClient(app) as client:
        resp = client.post("/api/crawl", json={"url": "https://example.com", "depth": 2})

    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    assert ctx.stats.as_dict()["crawl_requests"] == 1
    assert ctx.bg_manager.calls == ["crawl:https://example.com"]


def test_download_and_reclassify_routes_schedule_work():
    app, ctx = _make_app()

    with TestClient(app) as client:
        download = client.post("/api/download", json={"hashes": ["ABCDEF1234567890"]})
        reclassify = client.post("/api/reclassify", json={"hashes": ["ABCDEF1234567890"]})

    assert download.status_code == 200
    assert download.json()["count"] == 1
    assert reclassify.status_code == 200
    assert ctx.stats.as_dict()["download_requests"] == 1
    assert ctx.bg_manager.calls == ["download_selected", "reclassify"]


def test_search_clear_health_categories_and_config_routes():
    app, _ctx = _make_app()

    with TestClient(app) as client:
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


def test_errors_routes_return_and_clear_resolved_records():
    error_id = error_handler.record(
        ErrorCategory.QBIT,
        ErrorSeverity.ERROR,
        "route test error",
        {"source": "test"},
    )
    error_handler._errors[error_id].resolved = True

    app, _ctx = _make_app()

    try:
        with TestClient(app) as client:
            listed = client.get("/api/errors", params={"category": "qbit", "severity": "error"})
            cleared = client.post("/api/errors/clear")
    finally:
        error_handler._errors.pop(error_id, None)

    assert listed.status_code == 200
    assert listed.json()["errors"][0]["message"] == "route test error"
    assert cleared.status_code == 200
    assert cleared.json()["status"] == "cleared"


def test_update_config_replaces_qbit_client(monkeypatch):
    from magnet_harvester.api import routes as routes_module

    created = []

    class NewQbit(FakeQbit):
        def __init__(self, config):
            super().__init__()
            self.config = config
            created.append(self)

    monkeypatch.setattr(routes_module, "QBittorrentClient", NewQbit)

    app, ctx = _make_app()
    old_qbit = ctx.qbit

    with TestClient(app) as client:
        resp = client.put(
            "/api/config",
            json={
                "qbit_host": "http://localhost:8080",
                "qbit_username": "tester",
                "qbit_password": "secret",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["connected"] is True
    assert ctx.qbit is created[0]
    assert ctx.pipeline.replaced_qbit is created[0]
    assert old_qbit.closed is True


def test_update_config_keeps_current_client_when_candidate_cannot_connect(monkeypatch):
    from magnet_harvester.api import routes as routes_module

    class OfflineQbit(FakeQbit):
        def __init__(self, config):
            super().__init__()
            self.config = config
            self.ping_ok = False

    monkeypatch.setattr(routes_module, "QBittorrentClient", OfflineQbit)
    app, ctx = _make_app()
    old_qbit = ctx.qbit

    with TestClient(app) as client:
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
    assert ctx.qbit is old_qbit
    assert old_qbit.closed is False


def test_update_config_rejects_invalid_candidate_without_mutating_runtime():
    app, ctx = _make_app()
    old_qbit = ctx.qbit

    with TestClient(app) as client:
        resp = client.put(
            "/api/config",
            json={
                "qbit_host": "invalid-host",
                "qbit_username": "tester",
                "qbit_password": "secret",
            },
        )

    assert resp.status_code == 422
    assert ctx.qbit is old_qbit
    assert old_qbit.closed is False
