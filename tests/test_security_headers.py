"""基线安全响应头：所有 HTTP 响应都应带上，且不覆盖已有值。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from magnet_harvester.api.security import install_security_guard, install_security_headers

EXPECTED = {
    "content-security-policy": (
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'"
    ),
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def _app(*, guard: bool = False) -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/denied")
    async def denied():
        raise HTTPException(status_code=403, detail="denied")

    if guard:
        install_security_guard(app)
    # 最后装：作为最外层，护栏产生的 403 也带头
    install_security_headers(app)
    return app


def _assert_headers(response) -> None:
    for name, value in EXPECTED.items():
        assert response.headers[name] == value


@pytest.mark.parametrize(("path", "status"), [("/ok", 200), ("/denied", 403), ("/missing", 404)])
def test_http_responses_carry_baseline_headers(path: str, status: int) -> None:
    response = TestClient(_app()).get(path)
    assert response.status_code == status
    _assert_headers(response)


def test_existing_header_is_not_overwritten() -> None:
    app = _app()

    @app.get("/custom")
    async def custom():
        return PlainTextResponse("ok", headers={"X-Frame-Options": "SAMEORIGIN"})

    response = TestClient(app).get("/custom")
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_guard_rejection_carries_headers() -> None:
    response = TestClient(_app(guard=True)).get("/ok", headers={"host": "evil.example.com"})
    assert response.status_code == 403
    _assert_headers(response)


def test_index_and_static_responses_carry_headers() -> None:
    from magnet_harvester.main import app

    client = TestClient(app)
    for path in ("/", "/static/app.js"):
        response = client.get(path)
        assert response.status_code == 200
        _assert_headers(response)
