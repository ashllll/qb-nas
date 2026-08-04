"""WebSocket API Key authentication tests.

覆盖：配置 API_KEY 后 /ws 必须携带匹配的 api_key 查询参数；
错误/缺失 key 被拒绝（4401）；API_KEY 为空时保持兼容（直接可连）。
"""

from __future__ import annotations

import asyncio
import json
import warnings

import pytest
from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=StarletteDeprecationWarning,
)

from starlette.testclient import TestClient  # noqa: E402
from fastapi import WebSocketDisconnect  # noqa: E402

from tests.fixtures import make_test_app  # noqa: E402


@pytest.fixture
def app_ctx():
    """Create test app and yield (app, ctx) with cleanup."""
    app, ctx, _ = make_test_app()
    yield app, ctx
    if ctx.runtime.bg_manager:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(ctx.runtime.bg_manager.shutdown())
            loop.close()
        except Exception:
            pass


def test_ws_accepted_without_key_when_auth_disabled(app_ctx):
    """API_KEY 为空（默认）时，/ws 无需凭据即可连接（向后兼容）。"""
    app, ctx = app_ctx
    assert not ctx.runtime.api_key

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        raw = ws.receive_text()
        assert json.loads(raw)["type"] == "init"


def test_ws_rejected_without_key_when_auth_enabled(app_ctx):
    """配置 API_KEY 后，无 key 的连接必须被拒绝（4401）。"""
    app, ctx = app_ctx
    ctx.runtime.api_key = "test-secret-key-123"

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws"):
            pass
    assert exc.value.code == 4401


def test_ws_rejected_with_wrong_key(app_ctx):
    """错误 key 的连接必须被拒绝（4401）。"""
    app, ctx = app_ctx
    ctx.runtime.api_key = "test-secret-key-123"

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws?api_key=wrong-key"):
            pass
    assert exc.value.code == 4401


def test_ws_accepted_with_correct_key(app_ctx):
    """正确 key 的连接正常建立并收到 init 快照。"""
    app, ctx = app_ctx
    ctx.runtime.api_key = "test-secret-key-123"

    client = TestClient(app)
    with client.websocket_connect("/ws?api_key=test-secret-key-123") as ws:
        raw = ws.receive_text()
        assert json.loads(raw)["type"] == "init"


def test_ws_key_whitespace_tolerance_matches_rest(app_ctx):
    """与 REST 认证一致：两侧空白被 strip 后比较。"""
    app, ctx = app_ctx
    ctx.runtime.api_key = "  test-secret-key-123  "

    client = TestClient(app)
    with client.websocket_connect("/ws?api_key=test-secret-key-123") as ws:
        raw = ws.receive_text()
        assert json.loads(raw)["type"] == "init"


def test_ws_whitespace_only_key_disables_auth_like_rest(app_ctx):
    """与 REST 一致：strip 后为空的 key 配置视为未启用认证（兼容模式）。"""
    app, ctx = app_ctx
    ctx.runtime.api_key = "   "

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        raw = ws.receive_text()
        assert json.loads(raw)["type"] == "init"
