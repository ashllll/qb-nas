from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from magnet_harvester.config import Settings
from magnet_harvester.context.app_context import QBitReplacementTarget, QBitRuntime


@pytest.mark.parametrize(
    "url,canonical",
    [
        ("HTTP://NAS:80/", "http://nas/jsonrpc"),
        ("https://NAS:443/jsonrpc/", "https://nas/jsonrpc"),
        ("http://[2001:db8::1]:80/", "http://[2001:db8::1]/jsonrpc"),
        ("http://NAS:6800/jsonrpc", "http://nas:6800/jsonrpc"),
    ],
)
def test_bridge_configuration_normalization_is_idempotent(url, canonical):
    settings = Settings(_env_file=None)
    config = settings.build_aria2_config(rpc_url=url, secret="rpc", save_path="/vol1/")
    config.validate()
    assert config.rpc_url == canonical
    assert config.save_path == "/vol1/"


def test_bridge_configuration_roundtrips_without_reusing_qbit_credentials(tmp_path):
    path = tmp_path / ".env"
    path.write_text('QBIT_PASSWORD="legacy-password"\n')
    settings = Settings(_env_file=path, STORE_DB_PATH=str(tmp_path / "items.db"))
    assert settings.aria2.rpc_url == ""
    assert settings.aria2.secret == ""
    candidate = settings.build_aria2_config(
        rpc_url=" http://nas:6800/ ", secret="rpc-secret", save_path="/vol3/1000/Download"
    )
    assert candidate.rpc_url == "http://nas:6800/jsonrpc"
    assert candidate.state_db_path == str(tmp_path / "items.db")
    settings.persist_aria2_config(candidate)
    settings.commit_aria2_config(candidate)
    loaded = Settings(_env_file=path)
    assert loaded.aria2.rpc_url == candidate.rpc_url
    assert loaded.aria2.secret == "rpc-secret"
    assert loaded.aria2.save_path == "/vol3/1000/Download"
    assert loaded.QBIT_PASSWORD == "legacy-password"
    assert path.stat().st_mode & 0o777 == 0o600
    assert settings.build_aria2_config(rpc_url="http://new:6800").secret == "rpc-secret"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///tmp/socket",
        "http://",
        "http://nas:invalid",
        "http://user:secret@nas",
        "http://nas/jsonrpc?token=secret",
        "http://nas/#secret",
        "http://nas:70000",
    ],
)
def test_bridge_config_rejects_unsafe_endpoint_without_echoing_secrets(url):
    settings = Settings(_env_file=None)
    with pytest.raises(ValueError) as error:
        settings.build_aria2_config(rpc_url=url, secret="secret")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "path", ["/downloads", "../电影", "/vol3/../etc", "/vol3/x/../../etc", "/vol3/x\\y"]
)
def test_bridge_config_requires_native_nas_path(path):
    settings = Settings(_env_file=None)
    with pytest.raises(ValueError):
        settings.build_aria2_config(rpc_url="http://nas:6800", secret="rpc", save_path=path)


def test_bridge_config_requires_secret_and_preserves_omitted_secret():
    settings = Settings(
        _env_file=None, ARIA2_RPC_URL="http://nas:6800/jsonrpc", ARIA2_SECRET="existing"
    )
    assert settings.build_aria2_config().secret == "existing"
    with pytest.raises(ValueError):
        settings.build_aria2_config(secret="")


class Client:
    def __init__(self, config):
        self.config = config
        self.closed = False

    async def ping(self):
        return True

    async def close(self):
        self.closed = True


async def test_bridge_runtime_rollback_restores_file_settings_and_clients(tmp_path):
    path = tmp_path / ".env"
    settings = Settings(
        _env_file=path, ARIA2_RPC_URL="http://old:6800/jsonrpc", ARIA2_SECRET="old-secret"
    )
    settings.persist_aria2_config(settings.aria2)
    old = Client(settings.aria2)
    clients = []

    def factory(config):
        client = Client(config)
        clients.append(client)
        return client

    class Sync:
        async def replace_qbit_client(self, client):
            if client is not old:
                raise RuntimeError("sync failed")

    target = QBitReplacementTarget(qbit=old, qbit_sync=Sync())
    runtime = QBitRuntime(target=target, settings=settings, client_factory=factory)
    with pytest.raises(RuntimeError, match="sync failed"):
        await runtime.replace_aria2_config(
            rpc_url="http://new:6800", secret="new-secret", save_path=""
        )
    assert target.qbit is old
    assert not old.closed
    assert clients[0].closed
    assert Settings(_env_file=path).aria2.rpc_url == old.config.rpc_url
    assert settings.aria2.secret == "old-secret"


async def test_cancellation_during_old_client_cleanup_keeps_committed_candidate(tmp_path):
    from magnet_harvester.utils.bg_tasks import BGTaskManager

    path = tmp_path / ".env"
    settings = Settings(
        _env_file=path, ARIA2_RPC_URL="http://old:6800/jsonrpc", ARIA2_SECRET="old-secret"
    )
    settings.persist_aria2_config(settings.aria2)
    closing = asyncio.Event()

    class SlowCloseClient(Client):
        async def close(self):
            closing.set()
            await asyncio.Event().wait()

    old = SlowCloseClient(settings.aria2)
    target = QBitReplacementTarget(qbit=old)
    runtime = QBitRuntime(target=target, settings=settings, client_factory=Client)
    task = BGTaskManager.spawn(
        runtime.replace_aria2_config(rpc_url="http://new:6800", secret="new-secret", save_path="")
    )
    await closing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert target.qbit is not old
    assert not target.qbit.closed
    assert target.qbit.config.rpc_url == settings.aria2.rpc_url == "http://new:6800/jsonrpc"
    assert Settings(_env_file=path).aria2.secret == "new-secret"


@pytest.mark.parametrize("failure", [RuntimeError("probe failed"), asyncio.CancelledError()])
async def test_bridge_runtime_closes_candidate_if_probe_raises(tmp_path, failure):
    settings = Settings(_env_file=tmp_path / ".env")
    clients = []

    class FailingClient(Client):
        async def ping(self):
            raise failure

    def factory(config):
        client = FailingClient(config)
        clients.append(client)
        return client

    runtime = QBitRuntime(target=QBitReplacementTarget(), settings=settings, client_factory=factory)
    with pytest.raises(type(failure)):
        await runtime.replace_aria2_config(rpc_url="http://nas:6800", secret="rpc", save_path="")
    assert clients[0].closed
    assert not settings.config_file.exists()


async def test_bridge_config_read_never_returns_secret(monkeypatch):
    from magnet_harvester.api import routes

    monkeypatch.setattr(
        routes,
        "settings",
        SimpleNamespace(
            ARIA2_RPC_URL="http://nas:6800/jsonrpc", ARIA2_SECRET="top-secret", ARIA2_SAVE_PATH=""
        ),
    )
    assert await routes.get_config() == {
        "aria2_rpc_url": "http://nas:6800/jsonrpc",
        "aria2_secret_configured": True,
        "aria2_save_path": "",
    }
