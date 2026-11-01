"""缺陷 ⑥：DELETE /api/items 不带参数就等于清空全部。

修复后必须显式 ``?scope=all``；没有显式作用域时 400 并且一条都不删。
"""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.api.error_contract import install_error_handlers
from magnet_harvester.models import MagnetItem, TaskStatus
from tests._client import asgi_client
from tests.fixtures import make_test_app

SECRET = "delete-scope-key"


def _seed(ctx):
    async def _add():
        await ctx.core.store.add(
            MagnetItem(
                hash="KEEP0001",
                name="必须留下来的条目",
                magnet="magnet:?xt=urn:btih:KEEP0001",
                status=TaskStatus.success,
            )
        )

    asyncio.run(_add())


def _count(ctx) -> int:
    return asyncio.run(ctx.core.store.count())


@pytest.fixture
def client():
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    _seed(ctx)
    with asgi_client(app) as c:
        yield c, ctx


def test_delete_without_scope_refuses_and_keeps_everything(client):
    c, ctx = client
    ctx.runtime.api_key = ""

    resp = c.delete("/api/items")

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "delete_scope_required"
    assert "scope=all" in body["hint"]
    assert _count(ctx) == 1


def test_delete_with_unknown_scope_refuses(client):
    c, ctx = client
    ctx.runtime.api_key = ""

    resp = c.delete("/api/items", params={"scope": "selected"})

    assert resp.status_code == 400
    assert resp.json()["code"] == "delete_scope_required"
    assert _count(ctx) == 1


def test_delete_with_explicit_scope_all_clears(client):
    c, ctx = client
    ctx.runtime.api_key = ""

    resp = c.delete("/api/items", params={"scope": "all"})

    assert resp.status_code == 200
    assert _count(ctx) == 0


def test_delete_without_key_still_401_and_keeps_everything(client):
    c, ctx = client
    ctx.runtime.api_key = SECRET

    resp = c.delete("/api/items")

    assert resp.status_code == 401
    assert _count(ctx) == 1
