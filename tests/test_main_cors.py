from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from magnet_harvester import main


def test_configure_cors_before_startup(monkeypatch):
    """CORS 必须在 lifespan 前注册，否则 Starlette 会拒绝启动时加中间件。"""
    monkeypatch.setattr(
        main.settings, "CORS_ALLOWED_ORIGINS", " https://ui.example ,https://admin.example "
    )
    app = FastAPI()

    main._configure_cors(app)

    assert len(app.user_middleware) == 1
    middleware = app.user_middleware[0]
    assert middleware.cls is CORSMiddleware
    assert middleware.kwargs["allow_origins"] == ["https://ui.example", "https://admin.example"]

    with TestClient(app) as client:
        response = client.get("/", headers={"Origin": "https://ui.example"})

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "https://ui.example"
