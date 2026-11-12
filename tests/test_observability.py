"""Test ObservabilitySnapshot runtime payload composition."""

from __future__ import annotations

from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.services.observability import ObservabilitySnapshot
from magnet_harvester.services.stats import SystemStats
from magnet_harvester.store import AsyncItemStore, FakeStore
from tests.fixtures import FakeClassifier


class FakeQbit:
    def __init__(self, online: bool = True):
        self.online = online

    async def ping(self):
        return self.online

    def get_stats(self):
        return {"total_added": 3}


class FakeBroadcaster:
    active_count = 2


class FakeErrorHandler:
    def get_error_stats(self):
        return {"unique_errors": 1}


def test_system_status_counts_tracked_downloads():
    store = FakeStore()
    store.add(MagnetItem(hash="A", name="a", magnet="m", status=TaskStatus.pending))
    store.add(MagnetItem(hash="B", name="b", magnet="m", status=TaskStatus.adding))
    store.add(MagnetItem(hash="C", name="c", magnet="m", status=TaskStatus.queued))
    store.add(MagnetItem(hash="D", name="d", magnet="m", status=TaskStatus.downloading))
    store.add(MagnetItem(hash="E", name="e", magnet="m", status=TaskStatus.success))
    snapshot = ObservabilitySnapshot(store=AsyncItemStore(store), qbit=FakeQbit())

    import asyncio

    result = asyncio.run(snapshot.system_status())

    assert result["qbittorrent"] == "online"
    assert result["items_count"] == 5
    assert result["tracked_downloads"] == 3
    assert result["qbit_stats"] == {"total_added": 3}


def test_api_stats_combines_stats_with_runtime_context():
    store = FakeStore()
    stats = SystemStats()
    store.add(MagnetItem(hash="A", name="a", magnet="m"))
    snapshot = ObservabilitySnapshot(
        store=AsyncItemStore(store),
        qbit=FakeQbit(),
        stats=stats,
        broadcaster=FakeBroadcaster(),
        error_handler=FakeErrorHandler(),
    )

    import asyncio

    result = asyncio.run(snapshot.api_stats())

    assert result["api_calls"] == 1
    assert result["active_items"] == 1
    assert result["websocket_clients"] == 2
    assert result["error_stats"] == {"unique_errors": 1}


def test_health_reports_qbit_connectivity():
    snapshot = ObservabilitySnapshot(
        store=AsyncItemStore(FakeStore()), qbit=FakeQbit(online=False), classifier=FakeClassifier()
    )

    import asyncio

    result = asyncio.run(snapshot.health())

    assert result == {"healthy": False, "qbittorrent": False, "classifier": True}

    # 无 classifier 时应报告 classifier: False
    snapshot_no_classifier = ObservabilitySnapshot(
        store=AsyncItemStore(FakeStore()), qbit=FakeQbit(online=False)
    )
    result2 = asyncio.run(snapshot_no_classifier.health())
    assert result2["classifier"] is False


class HangingQbit:
    async def ping(self):
        import asyncio

        await asyncio.sleep(5)
        return True

    def get_stats(self):
        return {}


class CountingQbit(FakeQbit):
    def __init__(self, online: bool = True):
        super().__init__(online)
        self.pings = 0

    async def ping(self):
        self.pings += 1
        return await super().ping()


def test_health_does_not_wait_for_a_hanging_qbit():
    import asyncio
    import time

    from magnet_harvester.services.probe import CachedProbe

    snapshot = ObservabilitySnapshot(
        store=AsyncItemStore(FakeStore()),
        qbit=HangingQbit(),
        classifier=FakeClassifier(),
        probe=CachedProbe(ttl=10.0, timeout=0.05),
    )

    started = time.monotonic()
    result = asyncio.run(snapshot.health())

    assert result["qbittorrent"] is False
    assert time.monotonic() - started < 1.0


def test_health_and_status_share_one_cached_probe():
    import asyncio

    from magnet_harvester.services.probe import CachedProbe

    qbit = CountingQbit(online=False)
    snapshot = ObservabilitySnapshot(
        store=AsyncItemStore(FakeStore()),
        qbit=qbit,
        classifier=FakeClassifier(),
        probe=CachedProbe(ttl=60.0, timeout=1.0),
    )

    async def sample():
        health = await snapshot.health()
        status = await snapshot.system_status()
        again = await snapshot.health()
        return health, status, again

    health, status, again = asyncio.run(sample())

    assert health["qbittorrent"] is False
    assert status["qbittorrent"] == "offline"
    assert again["qbittorrent"] is False
    assert qbit.pings == 1
