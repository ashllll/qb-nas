"""Integration tests for the API-driven crawl pipeline."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from tests._client import asgi_client

from magnet_harvester.api.routes import router
from magnet_harvester.bus import NullBus
from magnet_harvester.context.app_context import AppContext
from magnet_harvester.models import MagnetItem
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.services.item_queries import ItemQueryExecutor
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.utils.bg_tasks import BGTaskManager


class FakeCrawler:
    max_depth = 2

    async def admit_url(self, url: str) -> str:
        return url

    async def crawl(self, url: str, depth: int = 1):
        yield {
            "type": "found",
            "item": MagnetItem(
                hash="ABCDEF1234567890",
                name="Example.Movie.2160p.BluRay",
                magnet="magnet:?xt=urn:btih:ABCDEF1234567890",
                source_url=url,
            ).model_dump(),
        }
        yield {"type": "done", "total": 1, "url": url}


class FakeClassifier:
    usage = type("Usage", (), {"as_dict": lambda self: {"total": 0}})()

    async def classify_stream_batch(self, items, on_result=None):
        if on_result is not None:
            on_result(
                0,
                {
                    "category": "电影",
                    "save_path": "电影",
                    "confidence": "high",
                    "reason": "integration",
                },
            )

    def get_cache_stats(self):
        return {}


class FakeQbit:
    last_error = None

    def __init__(self):
        self.added = []

    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool:
        self.added.append((magnet, category, save_path))
        return True

    async def ping(self) -> bool:
        return True

    def close(self):
        pass

    def is_healthy(self) -> bool:
        return True


def _make_integration_app():
    store = InMemoryItemStore()
    bus = NullBus()
    crawler = FakeCrawler()
    classifier = FakeClassifier()
    qbit = FakeQbit()
    bg_manager = BGTaskManager()
    transitions = MagnetItemTransitions(store=store, bus=bus)
    pipeline = HarvestPipeline(
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        store=store,
        bus=bus,
        task_manager=bg_manager,
        transitions=transitions,
    )
    action_executor = UserActionExecutor(
        store=store,
        pipeline=pipeline,
        task_manager=bg_manager,
        transitions=transitions,
    )
    ctx = AppContext(
        store=store,
        bus=bus,
        pipeline=pipeline,
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        bg_manager=bg_manager,
        action_executor=action_executor,
        item_queries=ItemQueryExecutor(store=store),
        item_transitions=transitions,
    )
    app = FastAPI()
    app.state.ctx = ctx
    app.include_router(router)
    return app, ctx, qbit


def test_api_crawl_auto_download_flow_reaches_qbit_and_items_view():
    app, ctx, qbit = _make_integration_app()

    with asgi_client(app) as client:
        started = client.post(
            "/api/crawl",
            json={"url": "https://example.com/source", "depth": 2, "auto_download": True},
        )
        assert started.status_code == 200
        task_id = started.json()["task_id"]

        for _ in range(20):
            client._loop.run_until_complete(asyncio.sleep(0.01))
            snapshot = ctx.bg_manager.get_task(task_id)
            if snapshot["status"] != "running":
                break

        assert ctx.bg_manager.get_task(task_id)["status"] == "completed"

        listed = client.get("/api/items")

    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["category"] == "电影"
    assert payload["items"][0]["status"] == "queued"
    assert qbit.added == [("magnet:?xt=urn:btih:ABCDEF1234567890", "电影", "电影")]
