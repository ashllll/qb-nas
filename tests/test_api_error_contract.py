"""约定第 2 条：非 2xx 一律 {detail, code, hint}；401 要分清缺失/不对。

这些断言在修复前会红：默认 FastAPI 只回 {"detail": "Invalid or missing API key"}，
既没有 code/hint，也分不清"没带 key"还是"key 不对"。
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from fastapi import FastAPI

from magnet_harvester.api.error_contract import install_error_handlers
from magnet_harvester.config import settings
from tests.fixtures import asgi_client
from tests.fixtures import make_test_app

SECRET = "unit-test-key-a1"


@pytest.fixture
def client(monkeypatch):
    # 这一组锁的是 strict（必须 key）下的错误体；N23 默认 trusted 见
    # tests/test_auth_trust_model.py。
    monkeypatch.setattr(settings, "AUTH_MODE", "strict")
    # 这里测的是错误体形状，不是授权：用显式开发开关让 strict + 未配 key 也能进路由。
    monkeypatch.setattr(settings, "ALLOW_INSECURE_WRITE_API", True)
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    with asgi_client(app) as c:
        yield c, ctx


def _assert_contract(body: dict) -> None:
    assert isinstance(body.get("detail"), str) and body["detail"]
    assert isinstance(body.get("code"), str) and body["code"]
    assert isinstance(body.get("hint"), str) and body["hint"]


def test_401_without_key_says_key_is_missing(client):
    c, ctx = client
    ctx.runtime.api_key = SECRET

    resp = c.put("/api/config", json={"aria2_rpc_url": "http://nas:6800/jsonrpc"})

    assert resp.status_code == 401
    body = resp.json()
    _assert_contract(body)
    assert body["code"] == "api_key_missing"
    assert "没有带" in body["detail"] or "缺少" in body["detail"]
    # 下一步指向程序通道（浏览器页面在局域网内不需要 key）
    assert "X-API-Key" in body["hint"]
    assert "菜单栏" not in body["hint"]


def test_401_with_wrong_key_says_key_is_invalid(client):
    c, ctx = client
    ctx.runtime.api_key = SECRET

    resp = c.put(
        "/api/config",
        json={"aria2_rpc_url": "http://nas:6800/jsonrpc"},
        headers={"X-API-Key": "definitely-wrong"},
    )

    assert resp.status_code == 401
    body = resp.json()
    _assert_contract(body)
    assert body["code"] == "api_key_invalid"
    assert "不正确" in body["detail"]
    assert "API_KEY" in body["hint"]
    assert "退出本机" not in body["hint"]


def test_401_never_echoes_the_supplied_key(client):
    c, ctx = client
    ctx.runtime.api_key = SECRET

    resp = c.put(
        "/api/config",
        json={"aria2_rpc_url": "http://nas:6800/jsonrpc"},
        headers={"X-API-Key": "leaky-key-value-123"},
    )

    assert "leaky-key-value-123" not in resp.text
    assert SECRET not in resp.text


def test_404_body_has_contract_fields(client):
    c, ctx = client
    ctx.runtime.api_key = ""

    resp = c.get("/api/tasks/does-not-exist")

    assert resp.status_code == 404
    body = resp.json()
    _assert_contract(body)
    assert body["code"] == "not_found"


def test_validation_error_names_the_field(client):
    c, ctx = client
    ctx.runtime.api_key = ""

    resp = c.post("/api/crawl", json={})

    assert resp.status_code == 422
    body = resp.json()
    _assert_contract(body)
    assert body["code"] == "validation_error"
    assert "url" in body["detail"]


def test_unhandled_exception_returns_id_without_leaking_details(caplog):
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("disk exploded while writing /tmp/secret.db")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    client = httpx.AsyncClient(base_url="http://testserver", transport=transport)
    with caplog.at_level(logging.ERROR, logger="magnet_harvester.api.error_contract"):
        resp = asyncio.run(client.get("/boom"))
        asyncio.run(client.aclose())

    assert resp.status_code == 500
    body = resp.json()
    _assert_contract(body)
    assert body["code"] == "internal_error"
    # 路径与异常文本只进日志；正文只留编号，编号能在日志里找到对应堆栈
    assert "/tmp/secret.db" not in resp.text
    assert "RuntimeError" not in resp.text
    error_id = body["detail"].rsplit("错误编号 ", 1)[-1].rstrip("）")
    assert error_id in body["hint"]
    assert error_id in caplog.text
    assert "RuntimeError" in caplog.text
