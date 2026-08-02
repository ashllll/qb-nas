"""Application runtime lifecycle behavior."""


def test_runtime_shutdown_waits_for_tasks_before_closing_resources():
    import asyncio

    from magnet_harvester.assembly import AppRuntime
    from magnet_harvester.context.app_context import AppContext, CoreServices, RuntimeState

    order = []

    class SyncLoop:
        async def stop(self):
            order.append("sync")

    class Tasks:
        async def shutdown(self):
            order.append("tasks")

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
    )

    asyncio.run(AppRuntime(ctx=ctx, sync_loop=SyncLoop()).stop())

    assert order == ["sync", "tasks", "crawler", "qbit"]
