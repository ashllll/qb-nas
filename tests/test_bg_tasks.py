"""
Test BGTaskManager — background task creation with exception logging.
"""

import sys
import os
import asyncio
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.utils.bg_tasks import BGTaskManager


@pytest.mark.asyncio
async def test_create_returns_task():
    async def dummy():
        return 42

    mgr = BGTaskManager()
    task = mgr.create(dummy(), name="test_dummy")

    assert isinstance(task, asyncio.Task)
    assert task.get_name() == "test_dummy"
    result = await task
    assert result == 42


@pytest.mark.asyncio
async def test_task_status_snapshot_lives_after_completion():
    async def dummy():
        return 42

    mgr = BGTaskManager()
    task = mgr.create(dummy(), name="crawl:https://example.com")
    task_id = task.task_id

    running = mgr.get_task(task_id)
    assert running["task_id"] == task_id
    assert running["name"] == "crawl:https://example.com"
    assert running["status"] == "running"

    await task
    completed = mgr.get_task(task_id)

    assert completed["status"] == "completed"
    assert completed["error"] is None
    assert mgr.active_count == 0


@pytest.mark.asyncio
async def test_successful_task_does_not_log(caplog):
    caplog.set_level(logging.ERROR)

    async def ok():
        return "ok"

    mgr = BGTaskManager()
    task = mgr.create(ok(), name="test_ok")
    result = await task

    assert result == "ok"
    assert "test_ok" not in caplog.text


@pytest.mark.asyncio
async def test_failing_task_logs_exception(caplog):
    caplog.set_level(logging.ERROR)

    async def boom():
        raise ValueError("intentional failure")

    mgr = BGTaskManager()
    task = mgr.create(boom(), name="test_boom")

    with pytest.raises(ValueError):
        await task

    assert "test_boom" in caplog.text
    assert "intentional failure" in caplog.text


@pytest.mark.asyncio
async def test_cancelled_task_does_not_log(caplog):
    caplog.set_level(logging.ERROR)

    async def slow():
        await asyncio.sleep(10)

    mgr = BGTaskManager()
    task = mgr.create(slow(), name="test_cancel")
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert "test_cancel" not in caplog.text


@pytest.mark.asyncio
async def test_shutdown_cancels_and_awaits_all_tasks():
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    mgr = BGTaskManager()
    mgr.create(slow(), name="slow")
    await asyncio.sleep(0)

    await mgr.shutdown()

    assert cancelled.is_set()
    assert mgr.active_count == 0


@pytest.mark.asyncio
async def test_shutdown_rejects_new_tasks_without_leaking_coroutine():
    mgr = BGTaskManager()
    await mgr.shutdown()

    async def work():
        return None

    with pytest.raises(RuntimeError, match="shutting down"):
        mgr.create(work(), name="late")
