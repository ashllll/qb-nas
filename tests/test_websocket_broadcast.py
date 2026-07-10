"""
P2-14: WebSocket 并发广播测试

缺陷: _on_event 中 await ws.send_text(data) 是串行执行的，慢客户端会阻塞其他客户端
修复: 使用 asyncio.gather 并发发送
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState
from magnet_harvester.api.websocket import WSBroadcaster
from magnet_harvester.bus import Event, EventType
from magnet_harvester.models import MagnetItem
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore


@pytest.mark.asyncio
async def test_broadcast_is_concurrent():
    """验证广播是并发执行的"""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def tracked_send(data):
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1

    # 创建 5 个 mock WebSocket
    for i in range(5):
        ws = MagicMock()
        ws.send_text = AsyncMock(side_effect=tracked_send)
        broadcaster.add(ws)

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))

    assert max_active > 1, f"广播应并发执行，实际最大并发 {max_active}"


@pytest.mark.asyncio
async def test_broadcast_removes_dead_clients():
    """验证死连接被移除"""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    ws_alive = MagicMock()
    ws_alive.send_text = AsyncMock()

    ws_dead = MagicMock()
    ws_dead.send_text = AsyncMock(side_effect=Exception("Connection closed"))

    broadcaster.add(ws_alive)
    broadcaster.add(ws_dead)

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))

    assert broadcaster.active_count == 1
    assert ws_alive in broadcaster._active_ws
    assert ws_dead not in broadcaster._active_ws


@pytest.mark.asyncio
async def test_broadcast_skips_disconnected_clients():
    """已断开的 WebSocket 不应再尝试 send_text。"""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    ws_alive = MagicMock()
    ws_alive.client_state = WebSocketState.CONNECTED
    ws_alive.send_text = AsyncMock()

    ws_disconnected = MagicMock()
    ws_disconnected.client_state = WebSocketState.DISCONNECTED
    ws_disconnected.send_text = AsyncMock()

    broadcaster.add(ws_alive)
    broadcaster.add(ws_disconnected)

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))

    ws_alive.send_text.assert_awaited_once()
    ws_disconnected.send_text.assert_not_awaited()
    assert ws_disconnected not in broadcaster._active_ws


@pytest.mark.asyncio
async def test_initial_snapshot_delivers_every_item_beyond_first_page():
    backend = InMemoryItemStore()
    backend.add_batch(
        [
            MagnetItem(
                hash=f"INIT-{index:04d}",
                name=f"Item {index:04d}",
                magnet=f"magnet:?xt=urn:btih:INIT-{index:04d}",
            )
            for index in range(501)
        ]
    )
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus, store=AsyncItemStore(backend))
    ws = MagicMock()
    ws.send_text = AsyncMock()

    await broadcaster.send_init_from_store(ws)

    messages = [json.loads(call.args[0]) for call in ws.send_text.await_args_list]
    delivered_hashes = {
        item["hash"] for message in messages for item in message.get("items", [])
    }
    assert len(delivered_hashes) == 501
    assert messages[0]["type"] == "init"
    assert messages[-1]["type"] == "init_done"


@pytest.mark.asyncio
async def test_connection_closes_when_initial_snapshot_is_incomplete():
    backend = InMemoryItemStore()
    backend.add_batch(
        [
            MagnetItem(
                hash=f"PARTIAL-{index:04d}",
                name=f"Partial {index:04d}",
                magnet=f"magnet:?xt=urn:btih:PARTIAL-{index:04d}",
            )
            for index in range(501)
        ]
    )
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus, store=AsyncItemStore(backend))
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock(side_effect=[None, RuntimeError("connection lost")])
    ws.close = AsyncMock()
    ws.receive_text = AsyncMock()

    await broadcaster.handle_connection(ws)

    ws.close.assert_awaited_once_with(code=1011, reason="initialization failed")
    ws.receive_text.assert_not_awaited()
    assert ws not in broadcaster._active_ws


@pytest.mark.asyncio
async def test_live_events_are_replayed_after_snapshot_pages():
    backend = InMemoryItemStore()
    backend.add_batch(
        [
            MagnetItem(
                hash=f"ORDER-{index:04d}",
                name=f"Order {index:04d}",
                magnet=f"magnet:?xt=urn:btih:ORDER-{index:04d}",
            )
            for index in range(501)
        ]
    )
    store = AsyncItemStore(backend)
    second_page_ready = asyncio.Event()
    release_second_page = asyncio.Event()

    class PausingStore:
        calls = 0

        async def count_and_page(self, **kwargs):
            self.calls += 1
            result = await store.count_and_page(**kwargs)
            if self.calls == 2:
                second_page_ready.set()
                await release_second_page.wait()
            return result

    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus, store=PausingStore())
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())

    connection = asyncio.create_task(broadcaster.handle_connection(ws))
    await second_page_ready.wait()
    backend.update("ORDER-0500", status="error", error_msg="new state")
    await broadcaster._on_event(
        Event(
            EventType.STORE_CHANGED,
            {"item": {"hash": "ORDER-0500", "status": "error", "error_msg": "new state"}},
        )
    )
    release_second_page.set()
    await connection

    messages = [json.loads(call.args[0]) for call in ws.send_text.await_args_list]
    page_index = next(
        index
        for index, message in enumerate(messages)
        if message["type"] == "init_page"
        and any(item["hash"] == "ORDER-0500" for item in message["items"])
    )
    live_index = next(
        index
        for index, message in enumerate(messages)
        if message["type"] == "store_changed" and message["item"]["hash"] == "ORDER-0500"
    )
    assert page_index < live_index
    assert messages[live_index]["item"]["status"] == "error"
