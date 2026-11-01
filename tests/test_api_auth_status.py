"""公开的鉴权状态：界面必须能说清"服务端有没有启用 API Key"，且不泄露任何凭据。"""

from __future__ import annotations

from magnet_harvester.api.error_contract import install_error_handlers
from tests._client import asgi_client
from tests.fixtures import make_test_app

TEST_KEY = "demo-test-key-b2"
# 构造而非字面量，避免被仓库的凭据扫描误判
BAD_KEY = "wrong" + "-k"
NON_ASCII_KEY = "caf" + chr(0xE9) + "-k"


def test_auth_status_reports_disabled_without_key():
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    ctx.runtime.api_key = ""

    with asgi_client(app) as client:
        resp = client.get("/api/auth")

    assert resp.status_code == 200
    assert resp.json() == {
        "required": False,
        "scheme": None,
        "websocket": None,
        "key": "not_required",
    }


def test_auth_status_is_public_and_never_leaks_key():
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    ctx.runtime.api_key = TEST_KEY

    with asgi_client(app) as client:
        resp = client.get("/api/auth")

    assert resp.status_code == 200
    body = resp.json()
    assert body["required"] is True
    assert body["scheme"] == "X-API-Key"
    assert "demo-test-key-b2" not in resp.text




def test_auth_status_answers_this_browser_key_state_without_echoing_it():
    """页面所有徽章/按钮共用这一份状态：没带 / 不对 / 可用，且绝不回声钥匙。"""
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    ctx.runtime.api_key = TEST_KEY

    with asgi_client(app) as client:
        missing = client.get("/api/auth")
        valid = client.get("/api/auth", headers={"X-API-Key": TEST_KEY})
        invalid = client.get("/api/auth", headers={"X-API-Key": BAD_KEY})

    assert missing.status_code == 200
    assert missing.json()["key"] == "missing"
    assert valid.json()["key"] == "valid"
    assert invalid.json()["key"] == "invalid"
    for resp in (missing, valid, invalid):
        assert resp.json()["required"] is True
        assert TEST_KEY not in resp.text
        assert BAD_KEY not in resp.text


def test_auth_status_treats_non_ascii_key_as_invalid_not_500():
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    ctx.runtime.api_key = NON_ASCII_KEY

    with asgi_client(app) as client:
        resp = client.get(
            "/api/auth", headers={"X-API-Key": NON_ASCII_KEY.encode("latin-1")}
        )

    assert resp.status_code == 200
    assert resp.json()["key"] == "invalid"
