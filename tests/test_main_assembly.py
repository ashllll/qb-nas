"""Main app should delegate runtime wiring to a dedicated assembly helper."""

from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent


def test_main_uses_dedicated_application_assembly_helper():
    main_source = (REPO_ROOT / "magnet_harvester/main.py").read_text(encoding="utf-8")

    assert "magnet_harvester.assembly" in main_source
    assert "build_runtime" in main_source or "assemble_runtime" in main_source


def test_assembly_module_exists():
    assembly_file = REPO_ROOT / "magnet_harvester/assembly.py"
    assert assembly_file.exists()


def test_runtime_shutdown_waits_for_tasks_before_closing_resources():
    import asyncio

    from magnet_harvester.assembly import AppRuntime
    from magnet_harvester.context.app_context import AppContext

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
        store=None,
        bus=None,
        pipeline=None,
        crawler=Crawler(),
        classifier=None,
        qbit=Qbit(),
        bg_manager=Tasks(),
    )

    asyncio.run(AppRuntime(ctx=ctx, sync_loop=SyncLoop()).stop())

    assert order == ["sync", "tasks", "crawler", "qbit"]
