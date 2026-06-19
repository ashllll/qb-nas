"""
P1-8: Bus 内存泄漏测试

缺陷: MessageBus.emit 中 pending 任务只加了无意义的 add_done_callback，
      事件频繁时积累大量未完成任务，导致内存泄漏
修复: 使用 asyncio.wait_for + asyncio.gather 替代 asyncio.wait，
      超时后显式取消 pending 任务
"""

import asyncio
from pathlib import Path
import pytest
from magnet_harvester.bus import MessageBus, Event, EventType


@pytest.mark.asyncio
async def test_emit_cancels_slow_subscribers():
    """验证慢订阅者会被取消，不会无限期运行"""
    bus = MessageBus()
    slow_completed = False

    async def slow_subscriber(event):
        nonlocal slow_completed
        await asyncio.sleep(2.0)  # 比 1s 超时更长
        slow_completed = True

    bus.subscribe(None, slow_subscriber)

    await bus.emit(Event(EventType.STORE_CHANGED, {"test": 1}))
    # 给一点时间让取消传播
    await asyncio.sleep(0.1)
    assert not slow_completed, "慢订阅者应被取消"


@pytest.mark.asyncio
async def test_emit_fast_subscribers_complete():
    """验证快订阅者正常完成"""
    bus = MessageBus()
    fast_completed = False

    async def fast_subscriber(event):
        nonlocal fast_completed
        await asyncio.sleep(0.1)
        fast_completed = True

    bus.subscribe(None, fast_subscriber)

    await bus.emit(Event(EventType.STORE_CHANGED, {"test": 1}))
    assert fast_completed, "快订阅者应正常完成"


@pytest.mark.asyncio
async def test_emit_many_events_no_leak():
    """验证高频事件不会积累任务"""
    bus = MessageBus()
    call_count = 0

    async def counting_subscriber(event):
        nonlocal call_count
        await asyncio.sleep(0.05)
        call_count += 1

    bus.subscribe(None, counting_subscriber)

    # 快速发射 50 个事件
    for i in range(50):
        await bus.emit(Event(EventType.STORE_CHANGED, {"test": i}))

    # 等待所有任务完成或取消
    await asyncio.sleep(0.5)
    # 所有事件都应该被处理（因为 0.05s < 1s 超时）
    assert call_count == 50, f"期望 50 次调用，实际 {call_count}"


def test_message_bus_uses_bg_task_manager_spawn():
    """MessageBus fan-out 不应直接裸用 asyncio.create_task。"""
    source = Path("magnet_harvester/bus.py").read_text(encoding="utf-8")
    assert "BGTaskManager.spawn" in source
    assert "asyncio.create_task" not in source
