"""
TDD 循环 4: 事件总线与状态转换的背压隔离
验证 MessageBus.emit() 不阻塞发送方
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.bus import MessageBus, Event, EventType


# ═══════════════════════════════════════════════════
# 示踪弹: 慢订阅者不应阻塞 emit()
# ═══════════════════════════════════════════════════


async def _slow_subscriber(event):
    """模拟慢订阅者"""
    await asyncio.sleep(10.0)  # 远超 5s 超时


async def test_emit_does_not_block_on_slow_subscriber():
    """即使订阅者处理很慢，emit() 也应在合理时间内返回"""
    bus = MessageBus()
    bus.subscribe(EventType.CRAWL_START, _slow_subscriber)

    event = Event(EventType.CRAWL_START, {"url": "http://test.com"})

    start = time.time()
    await bus.emit(event)
    elapsed = time.time() - start

    # 应在 6 秒内返回（超时 5s + 缓冲），而不是等待慢订阅者的 10 秒
    assert elapsed <= 6.0, f"emit() 被慢订阅者阻塞了 {elapsed:.1f} 秒"


# ═══════════════════════════════════════════════════
# 增量测试 2: 订阅者异常不应传播到 emit()
# ═══════════════════════════════════════════════════


async def _error_subscriber(event):
    """模拟抛出异常的订阅者"""
    raise RuntimeError("订阅者故障")


async def test_emit_survives_subscriber_exception():
    """即使订阅者抛出异常，emit() 也应成功完成"""
    bus = MessageBus()
    bus.subscribe(EventType.CRAWL_START, _error_subscriber)

    event = Event(EventType.CRAWL_START, {"url": "http://test.com"})

    # 不应抛出异常
    await bus.emit(event)


# ═══════════════════════════════════════════════════
# 增量测试 3: 多个订阅者时超时仍生效
# ═══════════════════════════════════════════════════


async def _fast_subscriber(event):
    pass


async def test_emit_with_mixed_subscribers():
    """混合快慢订阅者时，emit() 仍应超时返回"""
    bus = MessageBus()
    bus.subscribe(EventType.CRAWL_START, _fast_subscriber)
    bus.subscribe(EventType.CRAWL_START, _slow_subscriber)
    bus.subscribe(EventType.CRAWL_START, _fast_subscriber)

    event = Event(EventType.CRAWL_START, {"url": "http://test.com"})

    start = time.time()
    await bus.emit(event)
    elapsed = time.time() - start

    assert elapsed <= 6.0, f"emit() 被阻塞了 {elapsed:.1f} 秒"


if __name__ == "__main__":
    asyncio.run(test_emit_does_not_block_on_slow_subscriber())
    print("[PASS] test_emit_does_not_block_on_slow_subscriber")

    asyncio.run(test_emit_survives_subscriber_exception())
    print("[PASS] test_emit_survives_subscriber_exception")

    asyncio.run(test_emit_with_mixed_subscribers())
    print("[PASS] test_emit_with_mixed_subscribers")

    print("\n=== TDD Loop 4: MessageBus backpressure tests passed! ===")
