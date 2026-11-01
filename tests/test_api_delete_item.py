"""DELETE /api/items/{hash}：只删这一条，不动别的条目、不动 qB 队列。

用途：验收/清理测试条目（用户规则「测试条目用完删掉」），以及删掉一条不想留的记录。
整库清空仍走 ``DELETE /api/items?scope=all``，必须显式声明。
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from magnet_harvester.api.error_contract import install_error_handlers
from magnet_harvester.bus import MessageBus
from magnet_harvester.models import MagnetItem, TaskStatus
from tests._client import asgi_client
from tests.fixtures import make_test_app

SECRET = "delete-item-key"
HEX = "A" * 40
BASE32 = base64.b32encode(bytes.fromhex(HEX)).decode()
OTHER = "B" * 40


def _seed(ctx, hash_key: str, name: str) -> None:
    asyncio.run(
        ctx.core.store.add(
            MagnetItem(
                hash=hash_key,
                name=name,
                magnet=f"magnet:?xt=urn:btih:{hash_key}",
                status=TaskStatus.paused,
            )
        )
    )


@pytest.fixture
def client():
    bus = MessageBus()
    app, ctx, qbit = make_test_app(bus=bus)
    install_error_handlers(app)
    ctx.runtime.api_key = SECRET
    _seed(ctx, HEX, "测试条目：要删的")
    _seed(ctx, OTHER, "必须留下来的条目")
    with asgi_client(app) as c:
        yield c, ctx, qbit, bus


def _get(ctx, hash_key: str):
    return asyncio.run(ctx.core.store.get(hash_key))


def test_delete_one_item_keeps_the_rest(client):
    c, ctx, qbit, _ = client
    resp = c.delete(f"/api/items/{HEX}", headers={"X-API-Key": SECRET})
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"
    assert _get(ctx, HEX) is None
    assert _get(ctx, OTHER) is not None
    assert asyncio.run(ctx.core.store.count()) == 1
    assert qbit.added == [], "删记录不许碰 qB 队列"


def test_delete_accepts_base32_and_lowercase(client):
    c, ctx, _, _ = client
    resp = c.delete(f"/api/items/{BASE32.lower()}", headers={"X-API-Key": SECRET})
    assert resp.status_code == 200
    assert _get(ctx, HEX) is None


def test_delete_broadcasts_item_removed(client):
    c, _, _, bus = client
    events = []

    async def capture(event):
        events.append(event)

    bus.subscribe(None, capture)
    resp = c.delete(f"/api/items/{HEX}", headers={"X-API-Key": SECRET})
    assert resp.status_code == 200
    # 断言线上契约（事件名），不依赖枚举成员，改前/改后都能跑
    assert [event.type.value for event in events] == ["item_removed"]
    assert events[0].data["hash"] == HEX


def test_delete_unknown_hash_is_404_with_code(client):
    c, _, _, _ = client
    resp = c.delete(f"/api/items/{'C' * 40}", headers={"X-API-Key": SECRET})
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "item_not_found"
    assert body["detail"]


def test_delete_invalid_hash_is_400(client):
    c, _, _, _ = client
    resp = c.delete("/api/items/not-a-hash", headers={"X-API-Key": SECRET})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_infohash"


def test_delete_requires_api_key(client):
    c, ctx, _, _ = client
    resp = c.delete(f"/api/items/{HEX}")
    assert resp.status_code == 401
    assert _get(ctx, HEX) is not None
