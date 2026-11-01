"""幂等：同一 event_id 只能对应一条上报。

「重启不重收」「断网恢复补发」都靠 event_id 幂等。除了「重发返回第一次的结果」，
还要守住两条边界：

1. 同一 event_id 带不同内容再来（客户端事件号重复）→ 拒绝本次内容，不能悄悄覆盖记录、
   也不能把两条内容都入库；
2. 两个请求同时到达时也一样——检查与记录之间不能被并发插队。
"""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.bus import MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.jobs import JobStore
from magnet_harvester.models import IngestKind, IngestRequest
from magnet_harvester.services.ingest import IngestService
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions

HEX_A = "1111111111111111111111111111111111111111"
HEX_B = "2222222222222222222222222222222222222222"
MAGNET_A = f"magnet:?xt=urn:btih:{HEX_A}&dn=A"
MAGNET_B = f"magnet:?xt=urn:btih:{HEX_B}&dn=B"


class SlowStore(AsyncItemStore):
    """让 get 让出事件循环，逼出「两个请求同时看到还没处理过」的窗口。"""

    async def get(self, hash_key):
        await asyncio.sleep(0.02)
        return await super().get(hash_key)


class NoopCrawler:
    max_depth = 2

    async def crawl(self, url: str, depth: int = 1):
        yield {"type": "done", "total": 0, "url": url, "metrics": {}}


def build(tmp_path, store=None):
    store = store or AsyncItemStore(InMemoryItemStore())
    bus = MessageBus()
    jobs = JobStore(tmp_path / "jobs.db")
    service = IngestService(
        store=store,
        jobs=jobs,
        classifier=LocalClassifier(),
        crawler=NoopCrawler(),
        bus=bus,
        discovery=DiscoveryTransitions(store=store, bus=bus),
        classification=ClassificationTransitions(store=store, bus=bus),
    )
    return service, store, jobs


@pytest.mark.asyncio
async def test_same_event_id_same_value_is_duplicate(tmp_path):
    service, store, _jobs = build(tmp_path)
    first = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value=MAGNET_A)
    )
    replay = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value=MAGNET_A)
    )

    assert first["status"] == "accepted"
    assert replay["status"] == "duplicate"
    assert await store.count() == 1


@pytest.mark.asyncio
async def test_same_event_id_different_value_is_rejected_without_overwrite(tmp_path):
    service, store, jobs = build(tmp_path)
    first = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value=MAGNET_A)
    )
    conflict = await service.handle_event(
        IngestRequest(event_id="evt-1", kind=IngestKind.magnet, value=MAGNET_B)
    )

    assert first["status"] == "accepted"
    assert conflict["ok"] is False
    assert conflict["status"] == "rejected"
    assert "event_id" in conflict["reason"]
    assert await store.count() == 1, "另一条内容不能被悄悄入库"
    record = jobs.get_event("evt-1")
    assert record is not None and record.item_id == HEX_A, "第一次的记录不能被覆盖"


@pytest.mark.asyncio
async def test_concurrent_same_event_id_different_value_only_one_wins(tmp_path):
    service, store, jobs = build(tmp_path, store=SlowStore(InMemoryItemStore()))

    results = await asyncio.gather(
        service.handle_event(
            IngestRequest(event_id="evt-9", kind=IngestKind.magnet, value=MAGNET_A)
        ),
        service.handle_event(
            IngestRequest(event_id="evt-9", kind=IngestKind.magnet, value=MAGNET_B)
        ),
    )

    statuses = sorted(result["status"] for result in results)
    assert statuses == ["accepted", "rejected"], statuses
    assert await store.count() == 1
    record = jobs.get_event("evt-9")
    assert record is not None
    assert record.item_id in {HEX_A, HEX_B}
