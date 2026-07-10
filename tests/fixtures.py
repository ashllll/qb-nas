"""Shared test fixtures and factory helpers for integration tests.

Provides reusable Fake components (crawler, classifier, qbit) and
a _make_test_app() factory so every integration test doesn't re-define
the same boilerplate.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from typing import Any, Callable, Generator

import httpx
from fastapi import FastAPI

from magnet_harvester.api.routes import router
from magnet_harvester.api.websocket import router as ws_router, WSBroadcaster
from magnet_harvester.bus import MessageBus as RealMessageBus, NullBus, Event, EventType
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.context.app_context import AppContext, AppServices, CoreServices, RuntimeState
from magnet_harvester.models import MagnetItem
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.services.item_queries import ItemQueryExecutor
from magnet_harvester.services.user_actions import UserActionExecutor
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import (
    ClassificationTransitions,
    DiscoveryTransitions,
    DownloadTransitions,
)
from magnet_harvester.utils.bg_tasks import BGTaskManager

# ═══════════════════════════════════════════════════
# Fake components
# ═══════════════════════════════════════════════════


class FakeCrawler:
    """Produces a configurable set of fake magnet items."""
    max_depth = 2

    def __init__(self, items: list[MagnetItem] | None = None, fail: bool = False):
        self._items = items or [
            MagnetItem(
                hash="ABCDEF1234567890",
                name="Example.Movie.2160p.BluRay",
                magnet="magnet:?xt=urn:btih:ABCDEF1234567890",
                source_url="https://example.com/torrent/1",
            ),
        ]
        self._fail = fail

    async def admit_url(self, url: str) -> str:
        if self._fail:
            raise ValueError("simulated crawl admission failure")
        return url

    async def crawl(self, url: str, depth: int = 1):
        if self._fail:
            yield {"type": "error", "msg": "simulated crawler failure", "url": url}
            yield {"type": "done", "total": 0, "url": url}
            return
        for item in self._items:
            yield {"type": "found", "item": item.model_dump()}
        yield {"type": "done", "total": len(self._items), "url": url}


class FakeClassifier:
    """Always returns a fixed classification for every item."""
    usage = type("Usage", (), {"as_dict": lambda self: {"total": 0}})()

    def __init__(self, category: str = "电影", confidence: str = "high"):
        self._category = category
        self._confidence = confidence

    async def classify_stream_batch(self, items: list[dict], on_result: Callable | None = None):
        for item in items:
            idx = item.get("index", -1)
            if on_result and idx >= 0:
                on_result(idx, {
                    "category": self._category,
                    "save_path": self._category,
                    "confidence": self._confidence,
                    "reason": "test_fake",
                })

    def get_cache_stats(self) -> dict:
        return {}

    def classify_one(self, name: str) -> dict:
        return {
            "category": self._category,
            "save_path": self._category,
            "confidence": self._confidence,
            "reason": "test_fake",
        }

    async def ping(self) -> bool:
        return True


class FakeQbit:
    """Records added magnets for assertion."""
    last_error: str | None = None

    def __init__(self, fail_add: bool = False):
        self.added: list[tuple[str, str, str]] = []  # (magnet, category, save_path)
        self._fail_add = fail_add

    async def add_magnet(self, magnet: str, category: str, save_path: str) -> bool:
        if self._fail_add:
            self.last_error = "simulated qbit failure"
            return False
        self.added.append((magnet, category, save_path))
        return True

    async def ping(self) -> bool:
        return True

    def close(self):
        pass

    def is_healthy(self) -> bool:
        return True


class FakeBus:
    """Records emitted events for assertion."""
    def __init__(self):
        self.events: list[Event] = []

    def subscribe(self, event_type: EventType | None, callback):
        pass

    def unsubscribe(self, event_type: EventType | None, callback):
        pass

    async def emit(self, event: Event):
        self.events.append(event)


class FakeErrorHandler:
    """Records errors for assertion."""
    def __init__(self):
        self.errors: list[dict] = []

    def record(self, category, severity, message, details=None, exc=None):
        cat_val = category.value if hasattr(category, "value") else str(category)
        sev_val = severity.value if hasattr(severity, "value") else str(severity)
        self.errors.append({
            "category": cat_val,
            "severity": sev_val,
            "message": message,
        })

    def get_error_stats(self) -> dict:
        return {"total_errors": len(self.errors), "unique_errors": len(self.errors)}

    def clear_all(self):
        self.errors = []

    def get_recent_errors(self, category=None, severity=None, limit=50):
        filtered = self.errors
        if category:
            cat_val = category.value if hasattr(category, "value") else str(category)
            filtered = [e for e in filtered if e["category"] == cat_val]
        if severity:
            sev_val = severity.value if hasattr(severity, "value") else str(severity)
            filtered = [e for e in filtered if e["severity"] == sev_val]
        # Return objects with to_dict() like the real ErrorRecord
        class _FakeRecord:
            def __init__(self, data):
                self._data = data
            def to_dict(self):
                return self._data
        return [_FakeRecord(e) for e in filtered[:limit]]


class FakeStats:
    def __init__(self):
        self.crawl_count = 0
        self.download_count = 0
        self.api_call_count = 0

    def record_crawl(self):
        self.crawl_count += 1

    def record_download(self):
        self.download_count += 1

    def record_api_call(self):
        self.api_call_count += 1

    def as_dict(self) -> dict:
        return {
            "crawl_count": self.crawl_count,
            "download_count": self.download_count,
            "api_calls": self.api_call_count,
        }


# ═══════════════════════════════════════════════════
# Test app factory
# ═══════════════════════════════════════════════════


def make_test_app(
    store: InMemoryItemStore | None = None,
    bus: RealMessageBus | NullBus | None = None,
    crawler: FakeCrawler | None = None,
    classifier: FakeClassifier | LocalClassifier | None = None,
    qbit: FakeQbit | None = None,
    bg_manager: BGTaskManager | None = None,
    discovery: DiscoveryTransitions | None = None,
    classification: ClassificationTransitions | None = None,
    downloads: DownloadTransitions | None = None,
    pipeline: HarvestPipeline | None = None,
    action_executor: UserActionExecutor | None = None,
    error_handler: FakeErrorHandler | None = None,
    stats: FakeStats | None = None,
) -> tuple[FastAPI, AppContext, FakeQbit | None]:
    """Build a test FastAPI app with injected (fake) dependencies.

    Any component not provided gets a sensible default Fake. Returns
    (app, ctx, qbit) where qbit is the FakeQbit instance for assertions.
    """
    _backend = store or InMemoryItemStore()
    _store = AsyncItemStore(_backend)
    _bus = bus or NullBus()
    _crawler = crawler or FakeCrawler()
    _classifier = classifier or FakeClassifier()
    _qbit = qbit or FakeQbit()
    _bg_manager = bg_manager or BGTaskManager()
    _discovery = discovery or DiscoveryTransitions(store=_store, bus=_bus)
    _classification = classification or ClassificationTransitions(store=_store, bus=_bus)
    _downloads = downloads or DownloadTransitions(store=_store, bus=_bus)
    _pipeline = pipeline or HarvestPipeline(
        crawler=_crawler,
        classifier=_classifier,
        qbit=_qbit,
        store=_store,
        bus=_bus,
        task_manager=_bg_manager,
        discovery=_discovery,
        classification=_classification,
        downloads=_downloads,
    )
    _action_executor = action_executor or UserActionExecutor(
        store=_store,
        pipeline=_pipeline,
        task_manager=_bg_manager,
        discovery=_discovery,
        classification=_classification,
    )
    _error_handler = error_handler or FakeErrorHandler()
    _broadcaster = WSBroadcaster(bus=_bus, store=_store)

    ctx = AppContext(
        core=CoreServices(
            store=_store,
            bus=_bus,
            pipeline=_pipeline,
            crawler=_crawler,
            classifier=_classifier,
            qbit=_qbit,
        ),
        app_services=AppServices(
            action_executor=_action_executor,
            broadcaster=_broadcaster,
            item_queries=ItemQueryExecutor(store=_store),
        ),
        runtime=RuntimeState(
            bg_manager=_bg_manager,
            error_handler=_error_handler,
            stats=stats or FakeStats(),
        ),
    )
    app = FastAPI()
    app.state.ctx = ctx
    app.include_router(router)
    app.include_router(ws_router)
    return app, ctx, _qbit


# ═══════════════════════════════════════════════════
# Async-compatible test client wrapper
# ═══════════════════════════════════════════════════


class _TestClientWrapper:
    """Sync facade over httpx.AsyncClient for ASGI apps."""

    def __init__(self, client: httpx.AsyncClient, app: FastAPI, loop: asyncio.AbstractEventLoop):
        self._client = client
        self.app = app
        self._loop = loop

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._loop.run_until_complete(self._client.get(url, **kwargs))

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._loop.run_until_complete(self._client.post(url, **kwargs))

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._loop.run_until_complete(self._client.put(url, **kwargs))

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._loop.run_until_complete(self._client.delete(url, **kwargs))


@contextmanager
def asgi_client(
    app: FastAPI,
    base_url: str = "http://testserver",
) -> Generator[_TestClientWrapper, None, None]:
    """Create a synchronous test client for a FastAPI/ASGI application.

    Uses httpx.ASGITransport directly and manages the lifespan event loop.
    """
    loop = asyncio.new_event_loop()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(base_url=base_url, transport=transport)
    lifespan = app.router.lifespan_context(app)
    try:
        loop.run_until_complete(lifespan.__aenter__())
        yield _TestClientWrapper(client, app, loop)
    except Exception:
        loop.run_until_complete(lifespan.__aexit__(*sys.exc_info()))
        raise
    else:
        loop.run_until_complete(lifespan.__aexit__(None, None, None))
    finally:
        loop.run_until_complete(client.aclose())
        loop.close()
