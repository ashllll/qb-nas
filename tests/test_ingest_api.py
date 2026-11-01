"""采集中心 API：/api/v1/ingest、health、status、jobs。"""

from __future__ import annotations

import asyncio
import base64

from fastapi import FastAPI

from magnet_harvester.api.ingest_routes import router as ingest_router
from magnet_harvester.bus import MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.context.app_context import (
    AppContext,
    AppServices,
    CoreServices,
    RuntimeState,
)
from magnet_harvester.jobs import JobStore
from magnet_harvester.models import TaskStatus
from magnet_harvester.services.ingest import IngestService
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions
from tests._client import asgi_client

HEX = "0123456789ABCDEF0123456789ABCDEF01234567"
HEX_B32 = base64.b32encode(bytes.fromhex(HEX)).decode()
MAGNET = f"magnet:?xt=urn:btih:{HEX}&dn=Example.2024.1080p"
ROOT_URL = "https://example.com/torrents/search/4/sexart"
API_KEY = "test-key"


class FakeCrawler:
    max_depth = 2

    def __init__(self, events=None):
        self.events = events or []
        self.calls = []

    async def crawl(self, url: str, depth: int = 1):
        self.calls.append((url, depth))
        for event in self.events:
            yield event


class FakeQbit:
    async def ping(self):
        return False

    def get_stats(self):
        return {}


class FakeStoreCount:
    """count() 只用于健康检查的存储可用性。"""

    def __init__(self, inner):
        self._inner = inner

    async def count(self):
        return await self._inner.count()


def make_app(tmp_path, *, crawler=None, api_key=API_KEY):
    store = AsyncItemStore(InMemoryItemStore())
    bus = MessageBus()
    jobs = JobStore(tmp_path / "center.db")
    ingest = IngestService(
        store=store,
        jobs=jobs,
        classifier=LocalClassifier(),
        crawler=crawler or FakeCrawler(),
        bus=bus,
        discovery=DiscoveryTransitions(store=store, bus=bus),
        classification=ClassificationTransitions(store=store, bus=bus),
    )
    ctx = AppContext(
        core=CoreServices(
            store=store,
            bus=bus,
            pipeline=None,
            crawler=crawler or FakeCrawler(),
            classifier=LocalClassifier(),
            qbit=FakeQbit(),
        ),
        app_services=AppServices(ingest=ingest),
        runtime=RuntimeState(api_key=api_key),
    )
    app = FastAPI()
    app.include_router(ingest_router)
    app.state.ctx = ctx
    return app, ctx


def post_ingest(client, kind, value, event_id="evt-1", key=API_KEY):
    headers = {"X-API-Key": key} if key else {}
    return client.post(
        "/api/v1/ingest",
        json={"event_id": event_id, "kind": kind, "value": value},
        headers=headers,
    )


def test_ingest_requires_api_key(tmp_path):
    app, _ctx = make_app(tmp_path)

    with asgi_client(app) as client:
        resp = post_ingest(client, "magnet", MAGNET, key="")

    assert resp.status_code == 401


def test_magnet_ingest_returns_item_id_and_is_idempotent(tmp_path):
    app, _ctx = make_app(tmp_path)

    with asgi_client(app) as client:
        first = post_ingest(client, "magnet", MAGNET)
        replay = post_ingest(client, "magnet", MAGNET)
        same_hash = post_ingest(
            client, "magnet", f"magnet:?xt=urn:btih:{HEX_B32}", event_id="evt-2"
        )

    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert first.json()["item_id"] == HEX
    assert first.json()["ok"] is True
    assert replay.json()["status"] == "duplicate"
    assert same_hash.json()["status"] == "duplicate"
    assert same_hash.json()["item_id"] == HEX


def test_url_ingest_rejects_unsafe_target_with_reason(tmp_path):
    crawler = FakeCrawler()
    app, _ctx = make_app(tmp_path, crawler=crawler)

    with asgi_client(app) as client:
        resp = post_ingest(client, "url", "http://127.0.0.1:8899/admin")

    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "rejected"
    assert body["ok"] is False
    assert "不安全" in body["reason"]
    assert crawler.calls == []


def test_status_and_jobs_after_job_finishes(tmp_path):
    crawler = FakeCrawler(
        [
            {
                "type": "found",
                "item": {
                    "hash": HEX,
                    "name": "Batch.2024.1080p",
                    "magnet": MAGNET,
                    "detail_url": f"{ROOT_URL}/torrents/details/1",
                },
            },
            {"type": "done", "total": 1, "url": ROOT_URL, "metrics": {"pages_crawled": 1}},
        ]
    )
    app, ctx = make_app(tmp_path, crawler=crawler)
    service = ctx.app_services.ingest

    started = asyncio.run(service.ingest_url(ROOT_URL, source="mac", auto_start=False))
    asyncio.run(service._run_job(started["job_id"]))

    with asgi_client(app) as client:
        status = client.get("/api/v1/status", headers={"X-API-Key": API_KEY}).json()
        jobs = client.get("/api/v1/jobs", headers={"X-API-Key": API_KEY}).json()
        single = client.get(
            f"/api/v1/jobs/{started['job_id']}", headers={"X-API-Key": API_KEY}
        ).json()

    assert status["jobs"][0]["status"] == "done"
    assert status["jobs"][0]["summary"] == "从 1 个网址收到 1 条"
    assert status["job_counts"]["done"] == 1
    assert status["recent_items"][0]["hash"] == HEX
    assert status["recent_items"][0]["detail_url"] == f"{ROOT_URL}/torrents/details/1"
    assert status["recent_items"][0]["status"] == TaskStatus.pending.value
    assert jobs["counts"]["done"] == 1
    assert single["job_id"] == started["job_id"]


def test_health_needs_no_key_and_hides_credentials(tmp_path):
    app, _ctx = make_app(tmp_path)

    with asgi_client(app) as client:
        resp = client.get("/api/v1/health")

    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["version"]
    assert body["store"]["ok"] is True
    assert body["qbit"]["ok"] is False
    assert "reason" in body["qbit"]
    assert set(body) == {"ok", "version", "store", "qbit", "crawler"}
    assert API_KEY not in resp.text


def test_status_requires_api_key(tmp_path):
    app, _ctx = make_app(tmp_path)

    with asgi_client(app) as client:
        resp = client.get("/api/v1/status")

    assert resp.status_code == 401
