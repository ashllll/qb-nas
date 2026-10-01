"""
P2-14: WebSocket 并发广播测试

缺陷: _on_event 中 await ws.send_text(data) 是串行执行的，慢客户端会阻塞其他客户端
修复: 使用 asyncio.gather 并发发送
"""

import asyncio
import gc
import json
import warnings
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState
from magnet_harvester.api import websocket as ws_module
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
    """验证死连接被移除，并且**真正关闭**以便客户端重连。"""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    ws_alive = MagicMock()
    ws_alive.send_text = AsyncMock()

    ws_dead = MagicMock()
    ws_dead.send_text = AsyncMock(side_effect=Exception("Connection closed"))
    ws_dead.close = AsyncMock()

    broadcaster.add(ws_alive)
    broadcaster.add(ws_dead)

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))

    assert broadcaster.active_count == 1
    assert ws_alive in broadcaster._active_ws
    assert ws_dead not in broadcaster._active_ws
    # 只移出集合而不关闭 → 客户端收不到 onclose，永不自愈
    ws_dead.close.assert_awaited_once_with(code=1011, reason="send failed or timed out")


@pytest.mark.asyncio
async def test_broadcast_skips_disconnected_clients():
    """已断开的 WebSocket 不应再尝试 send_text，并应被关闭且不再登记。"""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    ws_alive = MagicMock()
    ws_alive.client_state = WebSocketState.CONNECTED
    ws_alive.send_text = AsyncMock()

    ws_disconnected = MagicMock()
    ws_disconnected.client_state = WebSocketState.DISCONNECTED
    ws_disconnected.send_text = AsyncMock()
    ws_disconnected.close = AsyncMock()

    broadcaster.add(ws_alive)
    broadcaster.add(ws_disconnected)

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))

    ws_alive.send_text.assert_awaited_once()
    ws_disconnected.send_text.assert_not_awaited()
    ws_disconnected.close.assert_awaited_once_with(code=1011, reason="send failed or timed out")
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
    delivered_hashes = {item["hash"] for message in messages for item in message.get("items", [])}
    assert len(delivered_hashes) == 501
    assert messages[0]["type"] == "init"
    assert messages[-1]["type"] == "init_done"


@pytest.mark.asyncio
async def test_broadcast_timeout_closes_client_and_keeps_it_registered_on_close_failure(
    monkeypatch,
):
    """广播超时（慢客户端）也必须关闭连接；关闭失败时不得提前解除登记。"""
    monkeypatch.setattr(ws_module, "_SEND_TIMEOUT", 0.02)
    monkeypatch.setattr(ws_module, "_CLOSE_TIMEOUT", 0.02)

    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    async def never_returns(_data):
        await asyncio.Event().wait()

    ws = MagicMock()
    ws.send_text = AsyncMock(side_effect=never_returns)
    ws.client_state = WebSocketState.CONNECTED
    ws.close = AsyncMock(side_effect=RuntimeError("close blocked"))
    broadcaster.add(ws)

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))

    ws.close.assert_awaited_once()
    # 关闭失败必须保留登记，否则该连接既不投递事件也无人再处理（僵尸连接）
    assert ws in broadcaster._active_ws, "关闭失败时应保留登记以便下次重试"


@pytest.mark.asyncio
async def test_broadcast_close_failure_is_retried_next_event(monkeypatch):
    """上一次关闭失败的连接，应在后续广播中被再次关闭。"""
    monkeypatch.setattr(ws_module, "_SEND_TIMEOUT", 0.02)
    monkeypatch.setattr(ws_module, "_CLOSE_TIMEOUT", 0.02)

    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    ws = MagicMock()
    ws.send_text = AsyncMock(side_effect=Exception("Connection closed"))
    ws.close = AsyncMock(side_effect=[RuntimeError("close blocked"), None])
    broadcaster.add(ws)

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))
    assert ws in broadcaster._active_ws

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 2}))

    assert ws.close.await_count == 2, "第二次广播应重试关闭"
    assert ws not in broadcaster._active_ws, "关闭成功后应解除登记"


@pytest.mark.asyncio
async def test_initialization_send_timeout_closes_connection(monkeypatch):
    """初始化阶段发送卡死必须有超时并关闭，不得无限期挂住连接。"""
    monkeypatch.setattr(ws_module, "_SEND_TIMEOUT", 0.02)
    monkeypatch.setattr(ws_module, "_CLOSE_TIMEOUT", 0.02)

    async def never_returns(_data):
        await asyncio.Event().wait()

    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock(side_effect=never_returns)
    ws.close = AsyncMock()
    ws.receive_text = AsyncMock()

    await asyncio.wait_for(broadcaster.handle_connection(ws), timeout=2.0)

    ws.close.assert_awaited_once_with(code=1011, reason="initialization failed")
    assert ws not in broadcaster._initializing_ws


@pytest.mark.asyncio
async def test_initialization_backlog_is_capped_and_stalled_client_closed(monkeypatch):
    """客户端初始化期间不读数据时，事件积压必须有界，超限即关闭连接。"""
    monkeypatch.setattr(ws_module, "_MAX_INIT_QUEUE_ITEMS", 5)
    monkeypatch.setattr(ws_module, "_SEND_TIMEOUT", 0.05)
    monkeypatch.setattr(ws_module, "_CLOSE_TIMEOUT", 0.05)

    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)
    ws = MagicMock()
    ws.send_text = AsyncMock(side_effect=asyncio.Event().wait)
    ws.close = AsyncMock()

    # 进入 initializing 状态：首屏发送挂起，接收循环尚未开始
    broadcaster._initializing_ws[ws] = []
    task = asyncio.create_task(broadcaster.send_init_from_store(ws))
    await asyncio.sleep(0)

    assert ws in broadcaster._initializing_ws, "测试前提：连接仍处于初始化阶段"
    for index in range(12):
        await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"n": index}))

    ws.close.assert_awaited_once_with(code=1013, reason="initialization backlog exceeded")
    assert ws not in broadcaster._initializing_ws

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # 队列上限为 5，积压不得超过上限 + 本次事件
    assert len(ws.send_text.await_args_list) <= 1


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


@pytest.mark.asyncio
async def test_cancelling_broadcast_reaps_every_client_coroutine():
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    async def blocked_send(_data):
        await asyncio.Event().wait()

    for _ in range(100):
        ws = MagicMock()
        ws.send_text = AsyncMock(side_effect=blocked_send)
        broadcaster.add(ws)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        task = asyncio.create_task(
            broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gc.collect()
        await asyncio.sleep(0)

    assert not [warning for warning in caught if "was never awaited" in str(warning.message)]
