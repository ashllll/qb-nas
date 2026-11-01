"""SQLite 并发写：两个 store（资源库 + 任务/事件表）共用一个文件，也不能写坏。

常驻服务里至少有三路同时写同一个 ``magnet_items.db``：
- 抓取任务写 crawl_jobs（JobStore）；
- 每发现一条磁力写资源库（ItemStore）；
- 用户点下载 / qB 同步更新状态（ItemStore）。
连接各自独立（短连接），靠 WAL + busy_timeout 撑住并发；这里用线程压一轮，
确认不会出现 "database is locked" 或丢写。
"""

from __future__ import annotations

import threading

from magnet_harvester.jobs import CrawlJob, JobStore
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import SQLiteItemStore

DB_NAME = "shared.db"
WORKERS = 8
ITEMS_PER_WORKER = 40


def _hash(worker: int, index: int) -> str:
    return f"{worker:02X}{index:038X}"


def test_concurrent_writes_across_item_and_job_stores(tmp_path):
    db_path = tmp_path / DB_NAME
    items = SQLiteItemStore(db_path)
    jobs = JobStore(db_path)
    errors: list[BaseException] = []

    def item_writer(worker: int) -> None:
        try:
            for index in range(ITEMS_PER_WORKER):
                hash_key = _hash(worker, index)
                assert items.add(
                    MagnetItem(
                        hash=hash_key,
                        name=f"item-{worker}-{index}",
                        magnet=f"magnet:?xt=urn:btih:{hash_key}",
                    )
                )
                # 边写边更新（模拟分类/下载状态转换）
                assert items.update(hash_key, category="电影", status=TaskStatus.classifying)
                assert items.update_if_status(
                    hash_key, {TaskStatus.classifying}, status=TaskStatus.pending
                )
        except BaseException as exc:  # pragma: no cover - 失败时把异常带回主线程
            errors.append(exc)

    def job_writer(worker: int) -> None:
        try:
            for index in range(ITEMS_PER_WORKER):
                job_id = f"job-{worker}-{index}"
                jobs.create(CrawlJob(job_id=job_id, url=f"https://example.com/{job_id}"))
                jobs.update(job_id, status="running", found=index)
                jobs.record_event(
                    event_id=job_id,
                    kind="url",
                    value_hash=job_id,
                    status="accepted",
                    job_id=job_id,
                )
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=item_writer, args=(worker,)) for worker in range(WORKERS)
    ] + [threading.Thread(target=job_writer, args=(worker,)) for worker in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"并发写入不应失败：{errors[:3]}"
    assert items.count == WORKERS * ITEMS_PER_WORKER
    assert jobs.counts()["total"] == WORKERS * ITEMS_PER_WORKER

    # 每一行都完整：没有半截事务留下的 category/status 不一致
    rows = items.list(limit=WORKERS * ITEMS_PER_WORKER + 1)
    assert len(rows) == WORKERS * ITEMS_PER_WORKER
    assert all(item.category == "电影" and item.status == TaskStatus.pending for item in rows)
