from __future__ import annotations

import asyncio
import threading

import pytest

from magnet_harvester.store import AsyncItemStore


class BlockingStore:
    blocks_event_loop = True

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def get(self, key: str) -> str:
        self.started.set()
        self.release.wait(timeout=1)
        return key


class InlineStore:
    def __init__(self):
        self.thread_id: int | None = None

    def get(self, key: str) -> str:
        self.thread_id = threading.get_ident()
        return key


@pytest.mark.asyncio
async def test_blocking_store_call_does_not_stop_event_loop():
    backend = BlockingStore()
    store = AsyncItemStore(backend)
    task = asyncio.create_task(store.get("item"))

    started_at = asyncio.get_running_loop().time()
    await asyncio.to_thread(backend.started.wait, 0.2)
    await asyncio.sleep(0)
    assert asyncio.get_running_loop().time() - started_at < 0.2
    backend.release.set()

    assert await task == "item"


@pytest.mark.asyncio
async def test_in_memory_style_store_stays_on_calling_thread():
    backend = InlineStore()
    store = AsyncItemStore(backend)

    assert await store.get("item") == "item"
    assert backend.thread_id == threading.get_ident()


@pytest.mark.asyncio
async def test_count_is_part_of_the_typed_async_interface():
    class CountBackend:
        count = 3

    store = AsyncItemStore(CountBackend())

    assert await store.count() == 3
