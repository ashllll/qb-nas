"""
BGTaskManager — wraps asyncio.create_task with exception logging.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class TaskSnapshot:
    task_id: str
    name: str
    status: str = "running"
    error: str | None = None
    created_at: float = 0.0
    finished_at: float | None = None

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class BGTaskManager:
    """Owns detached tasks from creation through application shutdown."""

    def __init__(self):
        self._tasks: set[asyncio.Task] = set()
        self._task_ids: dict[asyncio.Task, str] = {}
        self._snapshots: dict[str, TaskSnapshot] = {}
        self._closing = False

    def create(self, coro, name: str | None = None) -> asyncio.Task:
        if self._closing:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            raise RuntimeError("background task manager is shutting down")
        task_name = name or "background-task"
        task = asyncio.create_task(coro, name=task_name)
        task_id = uuid.uuid4().hex
        try:
            task.task_id = task_id
        except AttributeError:
            pass  # 非 CPython 运行时可能不支持 Task 动态属性
        self._tasks.add(task)
        self._task_ids[task] = task_id
        self._snapshots[task_id] = TaskSnapshot(
            task_id=task_id,
            name=task_name,
            created_at=time.time(),
        )
        task.add_done_callback(self._on_done)
        return task

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def get_task(self, task_id: str) -> dict | None:
        snapshot = self._snapshots.get(task_id)
        if snapshot is None:
            return None
        return snapshot.as_dict()

    async def shutdown(self) -> None:
        self._closing = True
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                log.error(
                    "后台任务关闭超时（%d 个任务未在 10 秒内完成），强制跳过", len(tasks)
                )

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        task_id = self._task_ids.pop(task, None)
        snapshot = self._snapshots.get(task_id or "")
        if snapshot is not None:
            snapshot.finished_at = time.time()

        if task.cancelled():
            if snapshot is not None:
                snapshot.status = "cancelled"
            return

        exc = task.exception()
        if exc is not None:
            if snapshot is not None:
                snapshot.status = "failed"
                snapshot.error = str(exc)
            log.error(f"后台任务 [{task.get_name()}] 异常: {exc}", exc_info=exc)
            return

        if snapshot is not None:
            snapshot.status = "completed"

    @staticmethod
    def spawn(coro, *, task_manager=None, name: str | None = None) -> asyncio.Task:
        """Create a background task, optionally tracked by a BGTaskManager.

        When task_manager is provided, uses manager.create() for lifecycle tracking.
        Otherwise creates a bare asyncio.Task with a done-callback that logs
        unhandled exceptions — a safe fallback for callers that don't need
        full lifecycle management.
        """
        if task_manager is not None:
            return task_manager.create(coro, name=name)
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(_log_task_exception)
        return task


def _log_task_exception(task: asyncio.Task) -> None:
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            log.error("后台任务 [%s] 异常: %s", task.get_name(), exc, exc_info=exc)
