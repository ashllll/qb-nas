"""N23 授权模型：局域网信任 / 严格模式 / Host 与跨源防护。

这些用例锁住用户定下的口径：

* 默认（trusted）局域网内写接口不带 key 也放行，带正确 key 仍放行，
  错 key 不阻断——key 只留给程序通道，不再是页面的门；
* 严格模式（未来外网）写必须带正确 ``X-API-Key``，页面因此只读；
* Host 只接受本机/局域网名与显式白名单（防 DNS rebinding，读也挡）；
* 浏览器发起的跨源写被拒（CSRF），没有 Origin 的程序请求放行；
* ``/api/auth`` 与 ``/api/config`` 不回声任何凭据。

判定实现只有一处：``magnet_harvester.utils.auth.resolve_auth``。
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from magnet_harvester.api.error_contract import install_error_handlers
from magnet_harvester.api.security import install_security_guard
from magnet_harvester.config import settings
from tests._client import asgi_client
from tests.fixtures import make_test_app

KEY = "trust-model-demo-key"
# 构造而非字面量，避免被仓库的凭据扫描误判
WRONG_KEY = "wrong" + "-key"
EVIL_HOST = "evil.example.com"
EVIL_ORIGIN = "http://evil.example.com"


def _app(monkeypatch, *, mode: str, api_key: str = KEY):
    monkeypatch.setattr(settings, "AUTH_MODE", mode)
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    install_security_guard(app)
    ctx.runtime.api_key = api_key
    return app, ctx


def _write(client, **kwargs):
    """一个幂等的写请求：清空空库（成功即代表这次写被授权）。"""
    return client.delete("/api/items", params={"scope": "all"}, **kwargs)


# ── trusted：局域网内直接读写 ───────────────────────────────────────────


def test_trusted_write_without_key_is_allowed(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        assert _write(client).status_code == 200


def test_trusted_write_with_correct_key_is_allowed(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        assert _write(client, headers={"X-API-Key": KEY}).status_code == 200


def test_trusted_write_with_wrong_key_is_not_blocked(monkeypatch):
    """信任模式下 key 不是门：错 key 也不阻断（程序通道兼容，不误伤）。"""
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        assert _write(client, headers={"X-API-Key": WRONG_KEY}).status_code == 200


def test_trusted_auth_payload_tells_the_page_it_can_write(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        resp = client.get("/api/auth")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "trusted"
    assert body["trusted"] is True
    assert body["write"] is True
    assert body["required"] is False
    assert body["key"] == "not_required"
    assert body["reason"] == "trusted_lan"
    assert KEY not in resp.text


def test_trusted_auth_payload_never_blames_a_missing_key(monkeypatch):
    """信任模式下即使 Host/Origin 不可信，也不该把原因说成「缺 key」。"""
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        resp = client.get("/api/auth", headers={"Origin": EVIL_ORIGIN})
    body = resp.json()
    assert body["write"] is False
    assert body["reason"] == "cross_origin"
    assert body["required"] is False
    assert body["key"] == "not_required"


def test_trusted_websocket_connects_without_any_key(monkeypatch):
    """页面在信任模式下直连 WS：不发 key，也能拿 hello + init。"""
    app, _ = _app(monkeypatch, mode="trusted")
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello"
        assert hello["auth_required"] is False
        assert hello["mode"] == "trusted"
        assert json.loads(ws.receive_text())["type"] == "init"


# ── strict：写必须带正确 key（页面只读） ────────────────────────────────


def test_strict_write_without_key_says_which_key_is_missing(monkeypatch):
    app, _ = _app(monkeypatch, mode="strict")
    with asgi_client(app) as client:
        resp = _write(client)
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "api_key_missing"
    assert "请求缺少 API Key" not in resp.text
    assert KEY not in resp.text


def test_strict_write_with_wrong_key_is_rejected(monkeypatch):
    app, _ = _app(monkeypatch, mode="strict")
    with asgi_client(app) as client:
        resp = _write(client, headers={"X-API-Key": WRONG_KEY})
    assert resp.status_code == 401
    assert resp.json()["code"] == "api_key_invalid"
    assert WRONG_KEY not in resp.text


def test_strict_write_with_correct_key_is_allowed(monkeypatch):
    app, _ = _app(monkeypatch, mode="strict")
    with asgi_client(app) as client:
        assert _write(client, headers={"X-API-Key": KEY}).status_code == 200


def test_strict_auth_payload_sends_the_page_readonly(monkeypatch):
    app, _ = _app(monkeypatch, mode="strict")
    with asgi_client(app) as client:
        anonymous = client.get("/api/auth").json()
        authorized = client.get("/api/auth", headers={"X-API-Key": KEY}).json()
    assert anonymous["mode"] == "strict"
    assert anonymous["required"] is True
    assert anonymous["write"] is False
    assert anonymous["trusted"] is False
    assert anonymous["key"] == "missing"
    assert anonymous["reason"] == "strict_mode"
    # 程序带对 key 时仍可写；页面拿不到 key，所以只读。
    assert authorized["write"] is True
    assert authorized["key"] == "valid"


def test_strict_without_configured_key_stays_open_like_before(monkeypatch):
    """严格模式但服务端没配 key：没有门可关，按开放处理（向后兼容）。"""
    app, _ = _app(monkeypatch, mode="strict", api_key="")
    with asgi_client(app) as client:
        assert _write(client).status_code == 200
        body = client.get("/api/auth").json()
    assert body["required"] is False
    assert body["key"] == "not_required"
    assert body["write"] is True
    assert body["reason"] == "no_key_configured"


def test_strict_websocket_rejects_missing_and_wrong_key(monkeypatch):
    app, _ = _app(monkeypatch, mode="strict")
    client = TestClient(app)
    for payload, expected in (
        ({"type": "auth", "api_key": ""}, "api_key_missing"),
        ({"type": "auth", "api_key": WRONG_KEY}, "api_key_invalid"),
    ):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws") as ws:
                ws.send_text(json.dumps(payload))
                ws.receive_text()
        assert exc.value.code == 4401
        assert exc.value.reason == expected


def test_strict_websocket_accepts_first_frame_key(monkeypatch):
    app, _ = _app(monkeypatch, mode="strict")
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "api_key": KEY}))
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello"
        assert hello["auth_required"] is True
        assert json.loads(ws.receive_text())["type"] == "init"


# ── DNS rebinding：Host 不在白名单，读写都拒绝 ──────────────────────────


def test_untrusted_host_cannot_write(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        resp = _write(client, headers={"Host": EVIL_HOST})
    assert resp.status_code == 403
    assert resp.json()["code"] == "untrusted_host"
    assert resp.json()["hint"]


def test_untrusted_host_cannot_read_data(monkeypatch):
    """rebinding 的攻击面是读：读接口也必须挡掉。"""
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        resp = client.get("/api/items", headers={"Host": EVIL_HOST})
    assert resp.status_code == 403
    assert resp.json()["code"] == "untrusted_host"


def test_trusted_hosts_config_allows_explicit_domain(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    monkeypatch.setattr(settings, "TRUSTED_HOSTS", "nas.example.com")
    with asgi_client(app) as client:
        assert _write(client, headers={"Host": "nas.example.com"}).status_code == 200


def test_untrusted_host_websocket_handshake_is_refused(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws", headers={"host": EVIL_HOST}) as ws:
            ws.receive_text()


# ── CSRF：跨源写拒绝，程序请求放行 ──────────────────────────────────────


def test_cross_origin_write_is_rejected(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        resp = _write(client, headers={"Origin": EVIL_ORIGIN})
    assert resp.status_code == 403
    assert resp.json()["code"] == "cross_origin_blocked"


def test_cross_site_fetch_metadata_is_rejected(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        resp = _write(client, headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "cross_origin_blocked"


def test_same_origin_browser_write_is_allowed(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        resp = _write(
            client,
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        )
    assert resp.status_code == 200


def test_program_write_without_origin_is_allowed(monkeypatch):
    """Mac 代理/curl 不发 Origin：Host 可信就放行，兼容不能破。"""
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        assert _write(client, headers={"X-API-Key": KEY}).status_code == 200


def test_cross_origin_write_is_rejected_even_in_strict_mode(monkeypatch):
    """key 正确也不能从别的站点发起写：key 不是 CSRF 的挡箭牌。"""
    app, _ = _app(monkeypatch, mode="strict")
    with asgi_client(app) as client:
        resp = _write(client, headers={"X-API-Key": KEY, "Origin": EVIL_ORIGIN})
    assert resp.status_code == 403
    assert resp.json()["code"] == "cross_origin_blocked"


# ── 配置暴露面：只回非敏感字段，密码绝不回显 ────────────────────────────


def test_config_is_open_and_never_echoes_password_in_strict_mode(monkeypatch):
    app, _ = _app(monkeypatch, mode="strict")
    monkeypatch.setattr(settings, "QBIT_HOST", "http://192.168.20.193:8085")
    monkeypatch.setattr(settings, "QBIT_USERNAME", "qb-user")
    monkeypatch.setattr(settings, "QBIT_PASSWORD", "super-secret-pass")
    with asgi_client(app) as client:
        resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json() == {
        "qbit_host": "http://192.168.20.193:8085",
        "qbit_username": "qb-user",
        "qbit_password_configured": True,
    }
    assert "super-secret-pass" not in resp.text


def test_config_rejects_dns_rebinding_host(monkeypatch):
    app, _ = _app(monkeypatch, mode="trusted")
    with asgi_client(app) as client:
        resp = client.get("/api/config", headers={"Host": EVIL_HOST})
    assert resp.status_code == 403
    assert resp.json()["code"] == "untrusted_host"
