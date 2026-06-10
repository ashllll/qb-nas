"""
BGTaskManager — wraps asyncio.create_task with exception logging.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class BGTaskManager:
    """Creates background tasks and logs unhandled exceptions."""

    def create(self, coro, name: str | None = None) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(self._on_done)
        return task

    @staticmethod
    def _on_done(task: asyncio.Task) -> None:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                log.error(f"后台任务 [{task.get_name()}] 异常: {exc}", exc_info=exc)
