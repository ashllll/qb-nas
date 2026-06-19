"""
Test context/app_context.py — AppContext, RuntimeContext, get_context.
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus
from magnet_harvester.context.app_context import AppContext, RuntimeContext, get_context


def test_appcontext_holds_all_deps():
    store = FakeStore()
    bus = NullBus()
    ctx = AppContext(
        store=store,
        bus=bus,
        pipeline=None,
        crawler=None,
        classifier=None,
        qbit=None,
    )
    assert ctx.store is store
    assert ctx.bus is bus


def test_get_context_from_request():
    class FakeApp:
        def __init__(self):
            self.state = type("State", (), {"ctx": None})()

    class FakeRequest:
        def __init__(self):
            self.app = FakeApp()

    store = FakeStore()
    ctx = AppContext(
        store=store,
        bus=NullBus(),
        pipeline=None,
        crawler=None,
        classifier=None,
        qbit=None,
    )

    req = FakeRequest()
    req.app.state.ctx = ctx

    assert get_context(req) is ctx


def test_runtime_context_replace_qbit_updates_pipeline():
    class FakeQbit:
        def __init__(self, name):
            self.name = name
            self.closed = False

        async def close(self):
            self.closed = True

    class FakePipeline:
        def __init__(self):
            self._qbit = None

        def replace_download_phase(self, new_qbit):
            self._qbit = new_qbit

    old_qbit = FakeQbit("old")
    new_qbit = FakeQbit("new")
    pipeline = FakePipeline()
    pipeline._qbit = old_qbit

    app_ctx = AppContext(
        store=FakeStore(),
        bus=NullBus(),
        pipeline=pipeline,
        crawler=None,
        classifier=None,
        qbit=old_qbit,
    )
    runtime = RuntimeContext(ctx=app_ctx)

    asyncio.run(runtime.replace_qbit(new_qbit))

    assert app_ctx.qbit is new_qbit
    assert pipeline._qbit is new_qbit
    assert old_qbit.closed is True


if __name__ == "__main__":
    test_appcontext_holds_all_deps()
    test_get_context_from_request()
    test_runtime_context_replace_qbit_updates_pipeline()
    print("=== app_context tests passed! ===")
