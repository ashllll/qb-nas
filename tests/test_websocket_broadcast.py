"""
P2-14 / #69: WebSocket 广播 —— 按连接有界队列 + 独立发送协程。

慢客户端只拖慢自己的 sender，不阻塞事件循环、其他客户端或 bus.emit。
"""

import asyncio
import gc
import json
import warnings
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState
from magnet_harvester.api.websocket import WSBroadcaster
from magnet_harvester.bus import Event, EventType
from magnet_harvester.models import MagnetItem
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore


async def _drain(broadcaster: WSBroadcaster) -> None:
    """等到所有在线客户端的队列都被 sender 消费完。"""
    await asyncio.gather(*(client.queue.join() for client in list(broadcaster._clients.values())))
    await asyncio.sleep(0)


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_broadcast_returns_without_waiting_for_slow_sends():
    """_on_event 只入队：慢客户端不能拖住事件回调。"""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    slow_ws = MagicMock()
    slow_ws.client_state = WebSocketState.CONNECTED

    async def slow_send(_data):
        await asyncio.sleep(0.5)

    slow_ws.send_text = AsyncMock(side_effect=slow_send)

    fast_ws = MagicMock()
    fast_ws.client_state = WebSocketState.CONNECTED
    fast_ws.send_text = AsyncMock()

    broadcaster.add(slow_ws)
    broadcaster.add(fast_ws)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))
    elapsed = loop.time() - started
    assert elapsed < 0.1, f"广播回调被发送拖慢：{elapsed:.3f}s"

    await _drain(broadcaster)
    fast_ws.send_text.assert_awaited_once()
    slow_ws.send_text.assert_awaited_once()
    broadcaster.shutdown()


@pytest.mark.asyncio
async def test_broadcast_removes_dead_clients():
    """发送失败的连接由 sender 清理。"""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    ws_alive = MagicMock()
    ws_alive.client_state = WebSocketState.CONNECTED
    ws_alive.send_text = AsyncMock()

    ws_dead = MagicMock()
    ws_dead.client_state = WebSocketState.CONNECTED
    ws_dead.send_text = AsyncMock(side_effect=Exception("Connection closed"))
    ws_dead.close = AsyncMock()

    broadcaster.add(ws_alive)
    broadcaster.add(ws_dead)

    await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))
    await _wait_until(lambda: ws_dead not in broadcaster._active_ws and ws_dead.close.await_count)

    assert broadcaster.active_count == 1
    assert ws_alive in broadcaster._active_ws
    assert ws_dead not in broadcaster._active_ws
    ws_dead.close.assert_awaited()
    assert ws_dead.close.await_args.kwargs["code"] == 1013
    broadcaster.shutdown()


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
    await _wait_until(lambda: ws_disconnected not in broadcaster._active_ws)

    ws_alive.send_text.assert_awaited_once()
    ws_disconnected.send_text.assert_not_awaited()
    assert ws_disconnected not in broadcaster._active_ws
    broadcaster.shutdown()


def _seed(backend, prefix: str, count: int = 501) -> None:
    for index in range(count):
        backend.add(
            MagnetItem(
                hash=f"{prefix}-{index:04d}",
                name=f"Item {prefix} {index:04d}",
                magnet=f"magnet:?xt=urn:btih:{prefix}-{index:04d}",
            )
        )


@pytest.mark.asyncio
async def test_initial_snapshot_delivers_every_item_beyond_first_page():
    backend = InMemoryItemStore()
    _seed(backend, "INIT")
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
async def test_connection_closes_when_initial_snapshot_is_incomplete():
    backend = InMemoryItemStore()
    _seed(backend, "PARTIAL")
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
    _seed(backend, "ORDER")
    store = AsyncItemStore(backend)
    second_page_ready = asyncio.Event()
    release_second_page = asyncio.Event()
    release_read = asyncio.Event()

    class PausingStore:
        calls = 0

        async def count_and_page(self, **kwargs):
            self.calls += 1
            result = await store.count_and_page(**kwargs)
            if self.calls == 2:
                second_page_ready.set()
                await release_second_page.wait()
            return result

    async def blocked_receive():
        await release_read.wait()
        raise WebSocketDisconnect()

    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus, store=PausingStore())
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=blocked_receive)

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
    await _wait_until(lambda: broadcaster._clients)
    await _drain(broadcaster)
    release_read.set()
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
    broadcaster.shutdown()


@pytest.mark.asyncio
async def test_shutdown_reaps_every_client_sender():
    bus = MagicMock()
    bus.subscribe = MagicMock()
    broadcaster = WSBroadcaster(bus)

    async def blocked_send(_data):
        await asyncio.Event().wait()

    for _ in range(100):
        ws = MagicMock()
        ws.client_state = WebSocketState.CONNECTED
        ws.send_text = AsyncMock(side_effect=blocked_send)
        broadcaster.add(ws)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"test": 1}))
        broadcaster.shutdown()
        gc.collect()
        await asyncio.sleep(0)

    assert broadcaster.active_count == 0
    assert broadcaster._clients == {}
    assert not [warning for warning in caught if "was never awaited" in str(warning.message)]
