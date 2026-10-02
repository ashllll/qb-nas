"""Application runtime lifecycle behavior."""


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

    class Clipboard:
        async def shutdown(self):
            order.append("clipboard")

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
            clipboard_monitor=Clipboard(),
            broadcaster=Broadcaster(),
        ),
    )

    asyncio.run(AppRuntime(ctx=ctx, sync_loop=SyncLoop()).stop())

    assert order == ["sync", "clipboard", "tasks", "broadcaster", "crawler", "qbit"]


def test_lifespan_refuses_non_loopback_bind_without_api_key(monkeypatch):
    """启动期鉴权强制必须依据真实绑定地址，而不是 .env 里的 SERVICE_HOST。

    回归背景：只读 SERVICE_HOST（.env.example 默认 127.0.0.1）时，
    按文档用 `uvicorn ... --host 0.0.0.0` 启动且 API_KEY 为空，会被误判为 loopback
    并放行 —— LAN 上出现无鉴权写接口（实测 GET /api/config 与
    DELETE /api/items 均无凭据返回 200）。

    run.py 现在会设置 MH_BOUND_HOST 告知真实绑定地址。
    """
    import asyncio

    import pytest

    from magnet_harvester.config import settings
    from magnet_harvester.main import app, lifespan

    monkeypatch.setenv("MH_BOUND_HOST", "0.0.0.0")
    monkeypatch.setattr(settings, "API_KEY", "")
    monkeypatch.setattr(settings, "ALLOW_INSECURE_WRITE_API", False)

    async def _enter_lifespan():
        async with lifespan(app):
            pass

    with pytest.raises(RuntimeError, match="non-loopback"):
        asyncio.run(_enter_lifespan())
