from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def smoke():
    spec = importlib.util.spec_from_file_location("smoke_production", "scripts/smoke_production.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_bridge_smoke_preserves_exact_task_identity(smoke, monkeypatch, tmp_path):
    calls = []

    class Client:
        def __init__(self, config):
            assert config.rpc_url == "http://nas:6800/jsonrpc"
            assert config.save_path == "/vol3/Download"
            assert config.state_db_path == str(tmp_path / "smoke.db")

        async def ping(self):
            return True

        async def add_magnet(self, magnet, category, save_path=""):
            calls.append((magnet, category, save_path))
            return True

        async def poll_torrent_snapshot_with_removed(self):
            return {
                smoke._SMOKE_HASH: {"download_state": {"torrent_state": "aria2:waiting"}}
            }, set()

        async def close(self):
            calls.append("closed")

    async def no_sleep(_):
        pass

    monkeypatch.setattr(smoke, "Aria2BridgeClient", Client)
    monkeypatch.setattr(smoke.asyncio, "sleep", no_sleep)
    await smoke._check_bridge(
        "http://nas:6800/jsonrpc", "secret", "/vol3/Download", tmp_path / "smoke.db", True
    )
    assert calls == [(smoke._PLACEHOLDER_MAGNET, "smoke_test", "smoke_test"), "closed"]
    assert all(ok for _, ok, _ in smoke._STEPS)


@pytest.mark.asyncio
async def test_bridge_smoke_readonly_does_not_submit(smoke, monkeypatch, tmp_path):
    class Client:
        def __init__(self, config):
            pass

        async def ping(self):
            return True

        async def close(self):
            pass

    monkeypatch.setattr(smoke, "Aria2BridgeClient", Client)
    await smoke._check_bridge("http://nas:6800/jsonrpc", "secret", "", Path(tmp_path), False)
    assert all(ok for _, ok, _ in smoke._STEPS)
