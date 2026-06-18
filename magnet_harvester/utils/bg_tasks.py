"""
BGTaskManager — wraps asyncio.create_task with exception logging.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class BGTaskManager:
    """Owns detached tasks from creation through application shutdown."""

    def __init__(self):
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    def create(self, coro, name: str | None = None) -> asyncio.Task:
        if self._closing:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            raise RuntimeError("background task manager is shutting down")
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    async def shutdown(self) -> None:
        self._closing = True
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                log.error(f"后台任务 [{task.get_name()}] 异常: {exc}", exc_info=exc)

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
