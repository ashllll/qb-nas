"""Integration tests: Clipboard monitor → store flow."""

from __future__ import annotations

import asyncio

from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.bus import MessageBus, Event
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.services.clipboard_monitor import ClipboardMonitor
from magnet_harvester.models import TaskStatus


class _CollectingBus(MessageBus):
    """Records emitted events for assertions."""

    def __init__(self):
        super().__init__()
        self.events: list[Event] = []

    async def emit(self, event: Event):
        self.events.append(event)
        await super().emit(event)


class _Ingestion:
    def __init__(self, classifier, transitions):
        self._classifier = classifier
        self._transitions = transitions

    async def ingest(self, items, *, auto_download=False):
        accepted = []
        for item in items:
            result = self._classifier.classify_one(item.name)
            category = result.get("category", "其他") or "其他"
            enriched = item.model_copy(
                update={
                    "category": category,
                    "save_path": result.get("save_path", category) or category,
                }
            )
            if await self._transitions.found(enriched):
                accepted.append(item.hash)
        return accepted


def test_clipboard_monitor_parses_and_stores_magnet():
    """ClipboardMonitor should extract magnet from clipboard text and store it."""
    store = InMemoryItemStore()
    bus = _CollectingBus()
    classifier = LocalClassifier()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    monitor = ClipboardMonitor(
        bus=bus,
        ingestion=_Ingestion(classifier, transitions),
        poll_interval=0.1,
    )

    # Simulate what happens when clipboard content arrives
    magnet_text = (
        "magnet:?xt=urn:btih:DEADBEEF1234567890ABCDEF1234567890ABCDEF"
        "&dn=Example+Movie+2024+2160p+BluRay"
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        items = monitor._magnet_sources.from_clipboard_text(magnet_text)
        assert len(items) >= 1

        item = items[0]
        loop.run_until_complete(monitor._handle_item(item))

        # Verify item is in store
        retrieved = store.get(item["hash"])
        assert retrieved is not None
        assert retrieved.category is not None
        assert retrieved.status == TaskStatus.pending
    finally:
        loop.close()


def test_clipboard_monitor_ignores_duplicates():
    """Same magnet copied twice should only be stored once."""
    store = InMemoryItemStore()
    bus = _CollectingBus()
    classifier = LocalClassifier()
    transitions = MagnetItemTransitions(store=store, bus=bus)

    monitor = ClipboardMonitor(
        bus=bus,
        ingestion=_Ingestion(classifier, transitions),
    )

    duplicate_hash = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        item = {
            "hash": duplicate_hash,
            "name": "Duplicate Movie",
            "magnet": f"magnet:?xt=urn:btih:{duplicate_hash}&dn=Duplicate+Movie",
        }

        # First time — should store
        loop.run_until_complete(monitor._handle_item(item))
        assert store.count == 1

        # Second time with same hash — should be rejected as duplicate
        loop.run_until_complete(monitor._handle_item(item))
        assert store.count == 1  # Still 1
    finally:
        loop.close()
