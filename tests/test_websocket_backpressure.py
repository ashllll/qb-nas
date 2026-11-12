"""
#69: WebSocket 反压——慢连接不拖住广播，积压队列有界。
"""

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest
from starlette.websockets import WebSocketState

import magnet_harvester.api.websocket as wsmod
from magnet_harvester.api.websocket import WSBroadcaster
from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.models import MagnetItem
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore


class RecordingWebSocket:
    """记录收到消息；send_text 是立即返回的。"""

    def __init__(self, delay: float = 0.0):
        self.sent: list[str] = []
        self.delay = delay
        self.closed: list[tuple[int, str]] = []
        self.client_state = WebSocketState.CONNECTED
        self.first_send_at: float | None = None

    async def send_text(self, data: str):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.first_send_at is None:
            self.first_send_at = time.monotonic()
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed.append((code, reason))


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_slow_client_is_cut_off_without_blocking_others(monkeypatch):
    """慢客户端 send 睡 10s：事件回调立即返回，正常客户端很快收到，慢连接被 1013 断开。"""
    monkeypatch.setattr(wsmod, "_SEND_TIMEOUT", 30.0)
    bus = MessageBus()
    broadcaster = WSBroadcaster(bus)
    slow = RecordingWebSocket(delay=10.0)
    normal = RecordingWebSocket()
    broadcaster.add(slow)
    broadcaster.add(normal)

    started = time.monotonic()
    for index in range(50):
        await bus.emit(Event(EventType.STORE_CHANGED, {"index": index}))
    emit_elapsed = time.monotonic() - started
    assert emit_elapsed < 0.1, f"50 次 bus.emit 被发送拖慢：{emit_elapsed:.3f}s"

    await _wait_until(lambda: slow.closed)
    assert slow.closed[0][0] == 1013
    assert slow not in broadcaster._active_ws
    assert slow not in broadcaster._clients

    await _wait_until(lambda: len(normal.sent) == 50)
    assert len(normal.sent) == 50, f"正常客户端收到 {len(normal.sent)} 条"
    first_latency = normal.first_send_at - started
    assert first_latency < 0.1, f"正常客户端首条延迟 {first_latency:.3f}s"
    broadcaster.shutdown()


@pytest.mark.asyncio
async def test_queue_is_bounded_under_event_flood(monkeypatch):
    """10k 事件不会让积压队列无限增长：超过上限即断开。"""
    monkeypatch.setattr(wsmod, "_SEND_TIMEOUT", 30.0)
    broadcaster = WSBroadcaster(MagicMock())
    slow = RecordingWebSocket(delay=10.0)
    broadcaster.add(slow)
    client = broadcaster._clients[slow]

    for index in range(10_000):
        await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"index": index}))

    assert client.queue.qsize() <= wsmod._CLIENT_QUEUE_MAX
    assert slow not in broadcaster._clients
    assert broadcaster._clients == {}
    await _wait_until(lambda: slow.closed)
    broadcaster.shutdown()


@pytest.mark.asyncio
async def test_initial_queue_is_bounded(monkeypatch):
    """初始化期间事件积压也不能无限增长。"""
    monkeypatch.setattr(wsmod, "_SEND_TIMEOUT", 30.0)
    broadcaster = WSBroadcaster(MagicMock())
    ws = RecordingWebSocket()
    broadcaster._register_initializing(ws)
    client = broadcaster._clients[ws]

    for index in range(wsmod._INIT_QUEUE_MAX + 5):
        await broadcaster._on_event(Event(EventType.STORE_CHANGED, {"index": index}))

    assert client.queue.qsize() <= wsmod._INIT_QUEUE_MAX
    assert ws not in broadcaster._clients
    await _wait_until(lambda: ws.closed)
    assert ws.closed[0][0] == 1013
    broadcaster.shutdown()


@pytest.mark.asyncio
async def test_large_initial_snapshot_is_fast_and_converges():
    """2000 条快照分页发送 <3s，且不触发 initial snapshot did not converge。"""
    backend = InMemoryItemStore()
    for index in range(2000):
        backend.add(
            MagnetItem(
                hash=f"SNAP-{index:04d}",
                name=f"Item {index}",
                magnet=f"magnet:?xt=urn:btih:SNAP-{index:04d}",
            )
        )
    broadcaster = WSBroadcaster(MagicMock(), store=AsyncItemStore(backend))
    ws = RecordingWebSocket()

    started = time.monotonic()
    await broadcaster.send_init_from_store(ws)
    elapsed = time.monotonic() - started

    messages = [json.loads(data) for data in ws.sent]
    delivered = {item["hash"] for message in messages for item in message.get("items", [])}
    assert elapsed < 3.0, f"2000 条快照耗时 {elapsed:.3f}s"
    assert len(delivered) == 2000
    assert messages[0]["type"] == "init"
    assert messages[-1]["type"] == "init_done"
    assert [m["type"] for m in messages].count("init_page") == 3
