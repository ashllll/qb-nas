"""公开的鉴权状态：界面必须能说清"服务端有没有启用 API Key"，且不泄露任何凭据。"""

from __future__ import annotations

from magnet_harvester.api.error_contract import install_error_handlers
from tests._client import asgi_client
from tests.fixtures import make_test_app

TEST_KEY = "demo-test-key-b2"


def test_auth_status_reports_disabled_without_key():
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    ctx.runtime.api_key = ""

    with asgi_client(app) as client:
        resp = client.get("/api/auth")

    assert resp.status_code == 200
    assert resp.json() == {"required": False, "scheme": None, "websocket": None}


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


