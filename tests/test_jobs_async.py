"""JobStore 的异步外壳：写库不占事件循环，update 只开一条连接。"""

from __future__ import annotations

import asyncio
import time

from magnet_harvester.jobs import AsyncJobStore, CrawlJob, JobStatus, JobStore


def test_update_reads_back_over_one_connection(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "jobs.db")
    store.create(CrawlJob(job_id="j1", url="https://example.com/a", status=JobStatus.queued))

    connects = 0
    original = store._connect

    def counting_connect():
        nonlocal connects
        connects += 1
        return original()

    monkeypatch.setattr(store, "_connect", counting_connect)
    updated = store.update("j1", status=JobStatus.running)

    assert updated is not None and updated.status == JobStatus.running
    assert connects == 1, "update 应在本连接里读回，而不是再开一条连接调 get()"


async def test_async_job_store_keeps_event_loop_free(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "jobs.db")
    jobs = AsyncJobStore(store)

    def slow_get_event(event_id: str):
        time.sleep(0.2)
        return None

    monkeypatch.setattr(store, "get_event", slow_get_event)
    task = asyncio.create_task(jobs.get_event("e1"))
    # 阻塞实现会把这 20ms 的 sleep 一起拖到 200ms 之后，那时 task 已经结束。
    await asyncio.sleep(0.02)
    assert not task.done(), "JobStore 的同步 SQLite 调用应在线程池里跑，事件循环要能照常调度"
    assert await task is None
