"""Integration tests using the real LocalClassifier with fake crawler/qbit."""

from __future__ import annotations

import asyncio

from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.models import MagnetItem
from magnet_harvester.store import InMemoryItemStore
from magnet_harvester.bus import NullBus
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.utils.bg_tasks import BGTaskManager
from tests.fixtures import FakeCrawler, FakeQbit, make_test_app


def test_real_classifier_categorizes_items():
    """Real LocalClassifier should assign correct categories to magnet items."""
    store = InMemoryItemStore()
    bus = NullBus()
    classifier = LocalClassifier()
    qbit = FakeQbit()
    bg_manager = BGTaskManager()
    transitions = MagnetItemTransitions(store=store, bus=bus)
    crawler = FakeCrawler(items=[
        MagnetItem(hash="HASH001", name="Test.Movie.2024.2160p.BluRay", magnet="magnet:?xt=urn:btih:HASH001"),
        MagnetItem(hash="HASH002", name="Some.Show.S01E02.1080p.WEB-DL", magnet="magnet:?xt=urn:btih:HASH002"),
        MagnetItem(hash="HASH003", name="Anime.Title.Ep01.1080p.Crunchyroll", magnet="magnet:?xt=urn:btih:HASH003"),
    ])
    pipeline = HarvestPipeline(
        crawler=crawler,
        classifier=classifier,
        qbit=qbit,
        store=store,
        bus=bus,
        task_manager=bg_manager,
        transitions=transitions,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(pipeline.execute("https://example.com/", depth=1))
    finally:
        loop.close()

    item1 = store.get("HASH001")
    assert item1 is not None
    assert item1.category == "电影"

    item2 = store.get("HASH002")
    assert item2 is not None
    assert item2.category == "电视剧"

    item3 = store.get("HASH003")
    assert item3 is not None
    assert item3.category == "动漫"


def test_real_classifier_with_fallback_category():
    """Unknown item names should fall back to '其他' category."""
    # Reuse make_test_app for standard infrastructure, swap classifier for real one
    app, ctx, qbit = make_test_app(classifier=LocalClassifier())

    # Add an unrecognizable item via the store
    ctx.store.add(MagnetItem(
        hash="HASH099", name="Random.Unrecognizable.File.Name",
        magnet="magnet:?xt=urn:btih:HASH099",
    ))

    # Trigger reclassify via pipeline (uses the real LocalClassifier)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ctx.pipeline.reclassify(["HASH099"]))
    finally:
        loop.close()

    item = ctx.store.get("HASH099")
    assert item is not None
    assert item.category == "其他", f"expected fallback '其他', got '{item.category}'"
