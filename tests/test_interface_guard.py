"""接口来源守卫：无鉴权写请求不得经非 loopback 网卡进入。

背景：API_KEY 为空时写接口不做鉴权（本地回环默认部署的兼容模式）。若服务被绑到
非 loopback 地址（尤其 `uvicorn ... --host 0.0.0.0` 这类覆盖 SERVICE_HOST 的启动
方式），LAN 上就会出现无鉴权写接口。启动期校验只在"应用能得知真实绑定地址"时
生效，本守卫依据 ASGI scope 的 server（该连接被接受的本机接口地址）逐请求判断，
与启动方式无关。
"""

from __future__ import annotations

import os
import sys

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.utils.interface_guard import (  # noqa: E402
    is_non_loopback_interface,
    needs_interface_guard,
    request_arrived_on_non_loopback_interface,
)

LAN_IP = "192.168.0.230"


class _ScopeOverride:
    """把 scope['server'] 改成指定本机接口地址后再交给内层应用。"""

    def __init__(self, app, server):
        self.app = app
        self.server = server

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            scope = dict(scope)
            scope["server"] = self.server
        await self.app(scope, receive, send)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("127.0.0.1", False),
        ("::1", False),
        ("localhost", False),
        ("127.0.0.53", False),
        (None, False),
        ("", False),
        # 通配地址无法判断接口，交由具体连接的 server 字段
        ("0.0.0.0", False),
        ("::", False),
        # 非 loopback
        (LAN_IP, True),
        ("10.0.0.5", True),
        ("172.16.3.4", True),
        ("[::ffff:192.168.0.1]", True),
    ],
)
def test_is_non_loopback_interface(address, expected):
    assert is_non_loopback_interface(address) is expected


def test_guard_scope_classification():
    assert needs_interface_guard({"type": "http", "method": "POST"}) is True
    assert needs_interface_guard({"type": "http", "method": "delete"}) is True
    assert needs_interface_guard({"type": "http", "method": "PATCH"}) is True
    assert needs_interface_guard({"type": "http", "method": "GET"}) is False
    assert needs_interface_guard({"type": "websocket"}) is True

    assert request_arrived_on_non_loopback_interface({"server": (LAN_IP, 8899)}) is True
    assert request_arrived_on_non_loopback_interface({"server": ("127.0.0.1", 8899)}) is False
    assert request_arrived_on_non_loopback_interface({}) is False


@pytest.fixture
def guard_settings(monkeypatch):
    """按需设置模块级 settings，并保证测试期间一直生效。

    守卫在**请求时**读取 settings，因此不能在返回 transport 前恢复原值。
    """
    import magnet_harvester.main as main_module

    def _apply(*, api_key: str, insecure: bool):
        monkeypatch.setattr(main_module.settings, "API_KEY", api_key, raising=False)
        monkeypatch.setattr(
            main_module.settings, "ALLOW_INSECURE_WRITE_API", insecure, raising=False
        )

    return _apply


def _guarded_app(server):
    """构造只含守卫中间件的最小应用，scope['server'] 可注入。"""
    import magnet_harvester.main as main_module

    inner = FastAPI()

    @inner.post("/api/probe")
    async def _write_probe():
        return {"ok": True}

    @inner.get("/api/probe")
    async def _read_probe():
        return {"ok": True}

    inner.middleware("http")(main_module._interface_guard)
    return httpx.ASGITransport(app=_ScopeOverride(inner, server))


def _request(transport, method: str, path: str = "/api/probe"):
    import asyncio

    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
            return await client.request(method, path)

    return asyncio.run(run())


def test_unauth_write_from_lan_interface_is_rejected(guard_settings):
    guard_settings(api_key="", insecure=False)
    resp = _request(_guarded_app((LAN_IP, 8899)), "POST")
    assert resp.status_code == 403, resp.text
    assert "non-loopback" in resp.json()["detail"]


def test_unauth_write_from_loopback_interface_is_allowed(guard_settings):
    guard_settings(api_key="", insecure=False)
    assert _request(_guarded_app(("127.0.0.1", 8899)), "POST").status_code == 200


def test_read_from_lan_interface_is_not_guarded(guard_settings):
    guard_settings(api_key="", insecure=False)
    assert _request(_guarded_app((LAN_IP, 8899)), "GET").status_code == 200


def test_configured_api_key_disables_interface_guard(guard_settings):
    guard_settings(api_key="secret", insecure=False)
    assert _request(_guarded_app((LAN_IP, 8899)), "POST").status_code == 200


def test_insecure_opt_in_disables_interface_guard(guard_settings):
    guard_settings(api_key="", insecure=True)
    assert _request(_guarded_app((LAN_IP, 8899)), "POST").status_code == 200


def test_host_from_argv_reads_uvicorn_cli_host(monkeypatch):
    from magnet_harvester.main import _host_from_argv

    monkeypatch.setattr(sys, "argv", ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8899"])
    assert _host_from_argv() == "0.0.0.0"

    monkeypatch.setattr(sys, "argv", ["uvicorn", "app:app", "--host=192.168.0.1", "--port", "8899"])
    assert _host_from_argv() == "192.168.0.1"

    # 未指定 --host（或 uvicorn.run 脚本路径）→ 取不到
    monkeypatch.setattr(sys, "argv", ["run.py"])
    assert _host_from_argv() is None
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app:app", "--port", "8899"])
    assert _host_from_argv() is None


def test_bound_host_prefers_explicit_contract(monkeypatch):
    """MH_BOUND_HOST 优先于 argv，argv 优先于 UVICORN_HOST。"""
    from magnet_harvester import main as main_module

    monkeypatch.setattr(sys, "argv", ["uvicorn", "app:app", "--host", "0.0.0.0"])
    monkeypatch.setenv("UVICORN_HOST", "10.0.0.9")
    monkeypatch.setenv("MH_BOUND_HOST", "192.168.0.1")
    assert main_module._bound_host() == "192.168.0.1"

    monkeypatch.delenv("MH_BOUND_HOST", raising=False)
    assert main_module._bound_host() == "10.0.0.9"

    monkeypatch.delenv("UVICORN_HOST", raising=False)
    assert main_module._bound_host() == "0.0.0.0"
