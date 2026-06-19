"""
P2-14: WebSocket 并发广播测试

缺陷: _on_event 中 await ws.send_text(data) 是串行执行的，慢客户端会阻塞其他客户端
修复: 使用 asyncio.gather 并发发送
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from starlette.websockets import WebSocketState
from magnet_harvester.api.websocket import WSBroadcaster
from magnet_harvester.bus import Event, EventType


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
