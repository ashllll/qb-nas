"""公开的访问状态：页面要能说清「现在能不能写、为什么不能」，且不泄露任何凭据。

N23 之后页面不再持钥匙，所以 ``/api/auth`` 回答的是**这份请求的写权限**：
``mode`` / ``trusted`` / ``write`` / ``required`` / ``key`` / ``reason``。
写接口（``utils/auth.py::require_api_key``）与这里共用同一份判定。
"""

from __future__ import annotations


from magnet_harvester.api.error_contract import install_error_handlers
from magnet_harvester.api.security import install_security_guard
from magnet_harvester.config import settings
from tests._client import asgi_client
from tests.fixtures import make_test_app

TEST_KEY = "demo-test-key-b2"
# 构造而非字面量，避免被仓库的凭据扫描误判
BAD_KEY = "wrong" + "-k"
NON_ASCII_KEY = "caf" + chr(0xE9) + "-k"


def _client(monkeypatch, *, mode: str, api_key: str):
    monkeypatch.setattr(settings, "AUTH_MODE", mode)
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    install_security_guard(app)
    ctx.runtime.api_key = api_key
    return app


def test_trusted_status_is_public_and_says_writable(monkeypatch):
    """默认信任模式：不带任何 key 也明确回答「可以写」，且不回声 key。"""
    app = _client(monkeypatch, mode="trusted", api_key=TEST_KEY)
    with asgi_client(app) as client:
        resp = client.get("/api/auth")

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "trusted"
    assert body["write"] is True
    assert body["required"] is False
    assert body["key"] == "not_required"
    assert TEST_KEY not in resp.text


def test_strict_status_answers_readonly_without_leaking_key(monkeypatch):
    app = _client(monkeypatch, mode="strict", api_key=TEST_KEY)
    with asgi_client(app) as client:
        missing = client.get("/api/auth")
        valid = client.get("/api/auth", headers={"X-API-Key": TEST_KEY})
        invalid = client.get("/api/auth", headers={"X-API-Key": BAD_KEY})

    assert missing.json()["write"] is False
    assert missing.json()["required"] is True
    assert missing.json()["key"] == "missing"
    assert valid.json()["write"] is True
    assert valid.json()["key"] == "valid"
    assert invalid.json()["key"] == "invalid"
    for resp in (missing, valid, invalid):
        assert resp.status_code == 200
        assert TEST_KEY not in resp.text
        assert BAD_KEY not in resp.text


def test_strict_status_without_configured_key_is_writable(monkeypatch):
    """严格模式但服务端没有 key：没有门可关，如实说可写。"""
    app = _client(monkeypatch, mode="strict", api_key="")
    with asgi_client(app) as client:
        body = client.get("/api/auth").json()
    assert body["required"] is False
    assert body["write"] is True
    assert body["key"] == "not_required"


def test_status_rejects_dns_rebinding_host(monkeypatch):
    """攻击者域名（Host 不可信）连状态都不该拿到：这是 rebinding 的入口。"""
    app = _client(monkeypatch, mode="trusted", api_key=TEST_KEY)
    with asgi_client(app) as client:
        resp = client.get("/api/auth", headers={"Host": "evil.example.com"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "untrusted_host"


def test_status_treats_non_ascii_key_as_invalid_not_500(monkeypatch):
    app = _client(monkeypatch, mode="strict", api_key=NON_ASCII_KEY)
    with asgi_client(app) as client:
        resp = client.get("/api/auth", headers={"X-API-Key": NON_ASCII_KEY.encode("latin-1")})
    assert resp.status_code == 200
    assert resp.json()["key"] == "invalid"
