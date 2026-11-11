"""Application runtime lifecycle behavior."""

import pytest


def test_runtime_shutdown_waits_for_tasks_before_closing_resources():
    import asyncio

    from magnet_harvester.assembly import AppRuntime
    from magnet_harvester.context.app_context import (
        AppContext,
        AppServices,
        CoreServices,
        RuntimeState,
    )

    order = []

    class SyncLoop:
        async def stop(self):
            order.append("sync")

    class Tasks:
        async def shutdown(self):
            order.append("tasks")

    class Ingest:
        async def shutdown(self):
            order.append("ingest")

    class Broadcaster:
        def shutdown(self):
            order.append("broadcaster")

    class Crawler:
        async def stop(self):
            order.append("crawler")

    class Qbit:
        async def close(self):
            order.append("qbit")

    ctx = AppContext(
        core=CoreServices(
            store=None,
            bus=None,
            pipeline=None,
            crawler=Crawler(),
            classifier=None,
            qbit=Qbit(),
        ),
        runtime=RuntimeState(bg_manager=Tasks()),
        app_services=AppServices(
            ingest=Ingest(),
            broadcaster=Broadcaster(),
        ),
    )

    asyncio.run(AppRuntime(ctx=ctx, sync_loop=SyncLoop()).stop())

    assert order == ["sync", "ingest", "tasks", "broadcaster", "crawler", "qbit"]


def test_runtime_start_propagates_sync_loop_failure():
    """同步循环起不来就不叫启动成功：其余步骤已各自降级，这里不该再吞异常。"""
    import asyncio

    from magnet_harvester.assembly import AppRuntime
    from magnet_harvester.context.app_context import (
        AppContext,
        AppServices,
        CoreServices,
        RuntimeState,
    )

    class Crawler:
        async def start(self):
            raise RuntimeError("爬虫起不来，降级")

    class SyncLoop:
        async def start(self):
            raise RuntimeError("后台同步起不来")

    ctx = AppContext(
        core=CoreServices(
            store=None,
            bus=None,
            pipeline=None,
            crawler=Crawler(),
            classifier=None,
            qbit=None,
        ),
        runtime=RuntimeState(),
        app_services=AppServices(ingest=None),
    )

    # 爬虫的失败被吞掉（降级），同步循环的失败必须冒出来（fail-fast）
    with pytest.raises(RuntimeError, match="后台同步起不来"):
        asyncio.run(AppRuntime(ctx=ctx, sync_loop=SyncLoop()).start())


def test_lifespan_does_not_swallow_runtime_start_failure(monkeypatch):
    """启动失败必须让服务起不来，不能带着假健康继续跑。"""
    import asyncio

    from fastapi import FastAPI

    import magnet_harvester.main as main_module

    class Runtime:
        ctx = object()

        async def start(self):
            raise RuntimeError("后台同步起不来")

        async def stop(self):
            raise AssertionError("启动失败后不应进入关闭流程")

    monkeypatch.setattr(main_module, "build_runtime", lambda: Runtime())

    async def run():
        async with main_module.lifespan(FastAPI()):
            raise AssertionError("启动失败时不应进入应用运行阶段")

    with pytest.raises(RuntimeError, match="后台同步起不来"):
        asyncio.run(run())
