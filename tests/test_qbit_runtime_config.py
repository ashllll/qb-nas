"""
Test QBitRuntime.replace_qbit_config — the deep runtime/service operation
behind PUT /api/config.
"""

from __future__ import annotations

import sys
import os
import asyncio

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import QBitConfig
from magnet_harvester.context.app_context import (
    AppContext,
    AppServices,
    CoreServices,
    QBitRuntime,
    RuntimeState,
)
from magnet_harvester.qbit_client._transport import QBitTransport
from magnet_harvester.services.observability import ObservabilitySnapshot


def test_qbit_runtime_owns_one_replacement_transaction():
    fields = QBitRuntime.__dataclass_fields__

    assert "transaction_lock" in fields
    assert "config_lock" not in fields
    assert "replacement_target" not in fields


class FakeQbit:
    def __init__(self, config: QBitConfig):
        self.config = config
        self.ping_ok = True
        self.closed = False

    async def ping(self) -> bool:
        return self.ping_ok

    async def close(self) -> None:
        self.closed = True


class FakeSettings:
    def __init__(self, *, persist_raise: Exception | None = None):
        self.persisted: list[QBitConfig] = []
        self.committed: list[QBitConfig] = []
        self.persist_raise = persist_raise

    def build_qbit_config(self, host, username, password):
        if not host:
            raise ValueError("主机地址不能为空")
        if not host.startswith(("http://", "https://")):
            raise ValueError(f"非法的主机地址: {host}")
        if not username:
            raise ValueError("用户名不能为空")
        if not password:
            raise ValueError("密码不能为空")
        return QBitConfig(host=host, username=username, password=password, fs_base_path="")

    def persist_qbit_config(self, config, env_path=None):
        if self.persist_raise is not None:
            raise self.persist_raise
        self.persisted.append(config)

    def commit_qbit_config(self, config):
        self.committed.append(config)


class FakePipeline:
    def __init__(self):
        self.replaced_qbit = None

    def replace_download_phase(self, new_qbit):
        self.replaced_qbit = new_qbit


class FakeSync:
    def __init__(self):
        self.qbit = None

    async def replace_qbit_client(self, new_qbit):
        self.qbit = new_qbit


class FakeClassifier:
    def get_cache_stats(self):
        return {}


def _make_runtime(
    *,
    old_qbit=None,
    settings=None,
    factory=None,
    app_services=None,
    qbit_sync=None,
):
    ctx = AppContext(
        core=CoreServices(
            store=None,
            bus=None,
            pipeline=FakePipeline(),
            crawler=None,
            classifier=None,
            qbit=old_qbit,
        ),
        app_services=app_services or AppServices(),
        runtime=RuntimeState(qbit_sync=qbit_sync or FakeSync()),
    )
    return QBitRuntime(
        ctx=ctx,
        settings=settings or FakeSettings(),
        client_factory=factory or FakeQbit,
    )


async def test_replace_qbit_config_rejects_invalid_input():
    runtime = _make_runtime()

    with pytest.raises(ValueError, match="非法的主机地址"):
        await runtime.replace_qbit_config(
            host="not-a-url",
            username="user",
            password="pass",
        )


async def test_replace_qbit_config_returns_failed_when_ping_fails():
    class OfflineQbit(FakeQbit):
        def __init__(self, config: QBitConfig):
            super().__init__(config)
            self.ping_ok = False

    runtime = _make_runtime(factory=OfflineQbit)
    result = await runtime.replace_qbit_config(
        host="http://offline.example:8080",
        username="user",
        password="pass",
    )

    assert result == {"status": "failed", "connected": False}
    assert runtime.settings.persisted == []
    assert runtime.settings.committed == []


async def test_replace_qbit_config_closes_new_client_on_ping_failure():
    created = []

    class OfflineQbit(FakeQbit):
        def __init__(self, config: QBitConfig):
            super().__init__(config)
            self.ping_ok = False
            created.append(self)

    runtime = _make_runtime(factory=OfflineQbit)
    await runtime.replace_qbit_config(
        host="http://offline.example:8080",
        username="user",
        password="pass",
    )

    assert len(created) == 1
    assert created[0].closed is True


async def test_replace_qbit_config_raises_oserror_and_closes_client_on_persist_failure():
    runtime = _make_runtime(settings=FakeSettings(persist_raise=OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        await runtime.replace_qbit_config(
            host="http://localhost:8080",
            username="user",
            password="pass",
        )

    assert runtime.ctx.core.qbit is None
    assert runtime.ctx.core.pipeline.replaced_qbit is None
    assert runtime.ctx.runtime.qbit_sync.qbit is None
    assert runtime.settings.committed == []


async def test_replace_qbit_config_closes_new_client_on_persist_failure():
    created = []

    class TrackedQbit(FakeQbit):
        def __init__(self, config: QBitConfig):
            super().__init__(config)
            created.append(self)

    runtime = _make_runtime(
        settings=FakeSettings(persist_raise=OSError("disk full")),
        factory=TrackedQbit,
    )

    with pytest.raises(OSError):
        await runtime.replace_qbit_config(
            host="http://localhost:8080",
            username="user",
            password="pass",
        )

    assert len(created) == 1
    assert created[0].closed is True


async def test_replace_qbit_config_replaces_and_commits_on_success():
    old_qbit = FakeQbit(QBitConfig(host="http://old:8080"))
    runtime = _make_runtime(old_qbit=old_qbit)

    result = await runtime.replace_qbit_config(
        host="http://new:8080",
        username="newuser",
        password="newpass",
    )

    assert result == {"status": "ok", "connected": True}
    assert runtime.ctx.core.qbit is not old_qbit
    assert runtime.ctx.core.qbit.config.host == "http://new:8080"
    assert runtime.ctx.core.pipeline.replaced_qbit is runtime.ctx.core.qbit
    assert runtime.ctx.runtime.qbit_sync.qbit is runtime.ctx.core.qbit
    assert old_qbit.closed is True
    assert runtime.settings.persisted == [runtime.ctx.core.qbit.config]
    assert runtime.settings.committed == [runtime.ctx.core.qbit.config]


async def test_replace_qbit_config_refreshes_observability_qbit():
    old_qbit = FakeQbit(QBitConfig(host="http://old:8080"))
    old_qbit.ping_ok = False
    observability = ObservabilitySnapshot(
        store=None,
        qbit=old_qbit,
        classifier=FakeClassifier(),
    )
    runtime = _make_runtime(
        old_qbit=old_qbit,
        app_services=AppServices(observability=observability),
    )

    before = await observability.health()
    assert before["qbittorrent"] is False

    await runtime.replace_qbit_config(
        host="http://new:8080",
        username="newuser",
        password="newpass",
    )

    after = await observability.health()
    assert after["qbittorrent"] is True


async def test_replace_qbit_config_does_not_replace_on_persist_failure():
    old_qbit = FakeQbit(QBitConfig(host="http://old:8080"))
    runtime = _make_runtime(
        old_qbit=old_qbit,
        settings=FakeSettings(persist_raise=OSError("disk full")),
    )

    with pytest.raises(OSError):
        await runtime.replace_qbit_config(
            host="http://new:8080",
            username="user",
            password="pass",
        )

    assert runtime.ctx.core.qbit is old_qbit
    assert old_qbit.closed is False


async def test_replace_qbit_config_rolls_back_dependents_when_runtime_swap_fails():
    created = []
    old_config = QBitConfig(host="http://old:8080")
    old_qbit = FakeQbit(old_config)

    class TrackedQbit(FakeQbit):
        def __init__(self, config: QBitConfig):
            super().__init__(config)
            created.append(self)

    class FailingSync:
        async def replace_qbit_client(self, new_qbit):
            raise RuntimeError("sync failed")

    runtime = _make_runtime(
        old_qbit=old_qbit,
        factory=TrackedQbit,
        qbit_sync=FailingSync(),
    )

    with pytest.raises(RuntimeError):
        await runtime.replace_qbit_config(
            host="http://new:8080",
            username="user",
            password="pass",
        )

    assert runtime.ctx.core.qbit is old_qbit
    assert runtime.ctx.core.pipeline.replaced_qbit is old_qbit
    assert created[0].closed is True
    assert [config.host for config in runtime.settings.persisted] == [
        "http://new:8080",
        "http://old:8080",
    ]
    assert runtime.settings.committed == [old_config]


async def test_replace_qbit_config_rolls_back_when_runtime_swap_is_cancelled():
    created = []
    old_config = QBitConfig(host="http://old:8080")
    old_qbit = FakeQbit(old_config)
    swap_started = asyncio.Event()

    class TrackedQbit(FakeQbit):
        def __init__(self, config: QBitConfig):
            super().__init__(config)
            created.append(self)

    class BlockingSync:
        def __init__(self):
            self.qbit = old_qbit

        async def replace_qbit_client(self, new_qbit):
            if new_qbit is not old_qbit:
                swap_started.set()
                await asyncio.Event().wait()
            self.qbit = new_qbit

    sync = BlockingSync()
    runtime = _make_runtime(
        old_qbit=old_qbit,
        factory=TrackedQbit,
        qbit_sync=sync,
    )
    task = asyncio.create_task(
        runtime.replace_qbit_config(
            host="http://new:8080",
            username="user",
            password="pass",
        )
    )
    await swap_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.ctx.core.qbit is old_qbit
    assert runtime.ctx.core.pipeline.replaced_qbit is old_qbit
    assert sync.qbit is old_qbit
    assert old_qbit.closed is False
    assert created[0].closed is True
    assert [config.host for config in runtime.settings.persisted] == [
        "http://new:8080",
        "http://old:8080",
    ]
    assert runtime.settings.committed == [old_config]


async def test_replace_qbit_config_serializes_concurrent_replacements():
    class SlowQbit(FakeQbit):
        active_pings = 0
        max_active_pings = 0

        async def ping(self) -> bool:
            type(self).active_pings += 1
            type(self).max_active_pings = max(
                type(self).max_active_pings,
                type(self).active_pings,
            )
            try:
                await asyncio.sleep(0.01)
                return True
            finally:
                type(self).active_pings -= 1

    runtime = _make_runtime(factory=SlowQbit)

    results = await asyncio.gather(
        runtime.replace_qbit_config(
            host="http://one:8080",
            username="user",
            password="pass",
        ),
        runtime.replace_qbit_config(
            host="http://two:8080",
            username="user",
            password="pass",
        ),
    )

    assert results == [
        {"status": "ok", "connected": True},
        {"status": "ok", "connected": True},
    ]
    assert SlowQbit.max_active_pings == 1
    assert [config.host for config in runtime.settings.persisted] == [
        "http://one:8080",
        "http://two:8080",
    ]


async def test_qbit_runtime_replaces_all_runtime_dependents():
    old_qbit = FakeQbit(QBitConfig(host="http://old:8080"))
    new_qbit = FakeQbit(QBitConfig(host="http://new:8080"))
    sync = FakeSync()
    pipeline = FakePipeline()
    runtime = _make_runtime(old_qbit=old_qbit, qbit_sync=sync)
    runtime.ctx.core.pipeline = pipeline

    await runtime.replace_qbit(new_qbit)

    assert runtime.ctx.core.qbit is new_qbit
    assert sync.qbit is new_qbit
    assert pipeline.replaced_qbit is new_qbit
    assert old_qbit.closed is True


async def test_replace_tolerates_close_failure():
    """旧客户端 close() 抛出异常时，replace() 仍成功完成。"""

    class BrokenQbit(FakeQbit):
        async def close(self) -> None:
            raise RuntimeError("close failed")

    old_qbit = BrokenQbit(QBitConfig(host="http://old:8080"))
    new_qbit = FakeQbit(QBitConfig(host="http://new:8080"))
    sync = FakeSync()
    pipeline = FakePipeline()
    runtime = _make_runtime(old_qbit=old_qbit, qbit_sync=sync)
    runtime.ctx.core.pipeline = pipeline

    await runtime.replace_qbit(new_qbit)

    assert runtime.ctx.core.qbit is new_qbit
    assert sync.qbit is new_qbit
    assert pipeline.replaced_qbit is new_qbit


async def test_replace_with_same_instance_does_not_close():
    """自替换守卫：传入同一个客户端实例时不会调用 close()。"""
    qbit = FakeQbit(QBitConfig(host="http://same:8080"))
    sync = FakeSync()
    pipeline = FakePipeline()
    runtime = _make_runtime(old_qbit=qbit, qbit_sync=sync)
    runtime.ctx.core.pipeline = pipeline

    await runtime.replace_qbit(qbit)

    assert runtime.ctx.core.qbit is qbit
    assert sync.qbit is qbit
    assert pipeline.replaced_qbit is qbit
    assert qbit.closed is False  # 不应被关闭


async def test_transport_close_clears_client_on_aclose_failure():
    """即使 aclose() 抛出异常，_client 也应被置为 None。"""

    class BrokenClient:
        is_closed = False
        cookies = httpx.Cookies()

        async def aclose(self):
            raise RuntimeError("network error")

        async def post(self, url, **kw):
            raise NotImplementedError

        async def request(self, method, url, **kw):
            raise NotImplementedError

    class DummyStats:
        consecutive_failures = 0
        last_success_time = 0
        last_failure_time = 0

    transport = QBitTransport(
        host="http://localhost:8080",
        username="user",
        password="pass",
        stats=DummyStats(),
    )
    transport._client = BrokenClient()

    await transport.close()

    assert transport._client is None
    assert transport._authenticated is False


if __name__ == "__main__":
    import asyncio

    tests = [
        test_replace_qbit_config_rejects_invalid_input,
        test_replace_qbit_config_returns_failed_when_ping_fails,
        test_replace_qbit_config_closes_new_client_on_ping_failure,
        test_replace_qbit_config_raises_oserror_and_closes_client_on_persist_failure,
        test_replace_qbit_config_closes_new_client_on_persist_failure,
        test_replace_qbit_config_replaces_and_commits_on_success,
        test_replace_qbit_config_does_not_replace_on_persist_failure,
        test_qbit_runtime_replaces_all_runtime_dependents,
    ]
    for t in tests:
        asyncio.run(t())
        print(f"[PASS] {t.__name__}")
