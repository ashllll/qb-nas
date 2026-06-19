"""
Test QBitRuntime.replace_qbit_config — the deep runtime/service operation
behind PUT /api/config.
"""

from __future__ import annotations

import sys
import os
import asyncio

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.config import QBitConfig
from magnet_harvester.context.app_context import AppContext, QBitReplacementTarget, QBitRuntime


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


def _make_runtime(*, old_qbit=None, settings=None, factory=None):
    ctx = AppContext(
        store=None,
        bus=None,
        pipeline=FakePipeline(),
        crawler=None,
        classifier=None,
        qbit=old_qbit,
        qbit_sync=FakeSync(),
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

    assert runtime.ctx.qbit is None
    assert runtime.ctx.pipeline.replaced_qbit is None
    assert runtime.ctx.qbit_sync.qbit is None
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
    assert runtime.ctx.qbit is not old_qbit
    assert runtime.ctx.qbit.config.host == "http://new:8080"
    assert runtime.ctx.pipeline.replaced_qbit is runtime.ctx.qbit
    assert runtime.ctx.qbit_sync.qbit is runtime.ctx.qbit
    assert old_qbit.closed is True
    assert runtime.settings.persisted == [runtime.ctx.qbit.config]
    assert runtime.settings.committed == [runtime.ctx.qbit.config]


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

    assert runtime.ctx.qbit is old_qbit
    assert old_qbit.closed is False


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


async def test_replacement_target_works_without_app_context():
    old_qbit = FakeQbit(QBitConfig(host="http://old:8080"))
    new_qbit = FakeQbit(QBitConfig(host="http://new:8080"))
    holder = {"qbit": old_qbit}
    sync = FakeSync()
    pipeline = FakePipeline()

    target = QBitReplacementTarget(
        lock=None,
        get_qbit=lambda: holder["qbit"],
        set_qbit=lambda value: holder.update(qbit=value),
        qbit_sync=sync,
        pipeline=pipeline,
    )

    await target.replace(new_qbit)

    assert holder["qbit"] is new_qbit
    assert sync.qbit is new_qbit
    assert pipeline.replaced_qbit is new_qbit
    assert old_qbit.closed is True


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
        test_replacement_target_works_without_app_context,
    ]
    for t in tests:
        asyncio.run(t())
        print(f"[PASS] {t.__name__}")
