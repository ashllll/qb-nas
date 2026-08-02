"""
WebSocket broadcaster — manages active connections and broadcasts events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from magnet_harvester.bus import Event, MessageBus
from magnet_harvester.store import ItemStore
from magnet_harvester.utils.serializers import item_payload

log = logging.getLogger(__name__)
router = APIRouter()

_INIT_PAGE_SIZE = 500


def _json_serializer(obj: Any) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class WSBroadcaster:
    """Subscribes to MessageBus and broadcasts events to all active WebSocket clients."""

    def __init__(self, bus: MessageBus, store: ItemStore | None = None):
        self._bus = bus
        self._store = store
        self._active_ws: set[WebSocket] = set()
        self._initializing_ws: dict[WebSocket, list[str]] = {}
        bus.subscribe(None, self._on_event)

    def add(self, ws: WebSocket):
        self._active_ws.add(ws)

    def remove(self, ws: WebSocket):
        self._active_ws.discard(ws)
        self._initializing_ws.pop(ws, None)

    def shutdown(self):
        """取消 MessageBus 订阅，断开强引用以允许 GC 回收。

        架构支持 Broadcaster 热替换或重载时必须调用；当前单例场景下可选。
        """
        self._bus.unsubscribe(None, self._on_event)

    @property
    def active_count(self) -> int:
        return len(self._active_ws)

    async def send_init(self, ws: WebSocket, items: list):
        data = json.dumps(
            {"type": "init", "items": items}, ensure_ascii=False, default=_json_serializer
        )
        await ws.send_text(data)

    async def send_init_from_store(self, ws: WebSocket):
        if self._store is None:
            await self.send_init(ws, [])
            await self._send_init_done(ws, 0)
            return

        offset = 0
        delivered: set[str] = set()
        first_page = True
        while True:
            total, page = await self._store.count_and_page(
                limit=_INIT_PAGE_SIZE,
                offset=offset,
            )
            payloads = [item_payload(item) for item in page if item.hash not in delivered]
            delivered.update(item["hash"] for item in payloads)
            if first_page:
                await self.send_init(ws, payloads)
                first_page = False
            elif payloads:
                await ws.send_text(
                    json.dumps(
                        {"type": "init_page", "items": payloads},
                        ensure_ascii=False,
                        default=_json_serializer,
                    )
                )

            offset += len(page)
            if offset < total:
                continue
            if len(delivered) >= total:
                await self._send_init_done(ws, len(delivered))
                return
            if not page:
                raise RuntimeError("initial snapshot did not converge")
            offset = 0

    async def _send_init_done(self, ws: WebSocket, total: int) -> None:
        await ws.send_text(
            json.dumps(
                {"type": "init_done", "total": total},
                ensure_ascii=False,
            )
        )

    async def _finish_initialization(self, ws: WebSocket) -> None:
        while True:
            queued = self._initializing_ws.get(ws)
            if queued is None:
                return
            if queued:
                batch = list(queued)
                queued.clear()
                for data in batch:
                    await ws.send_text(data)
                continue
            self._initializing_ws.pop(ws, None)
            self._active_ws.add(ws)
            return

    async def handle_connection(self, ws: WebSocket):
        """Full WebSocket lifecycle: accept, init, keep-alive, cleanup."""
        await ws.accept()
        self._initializing_ws[ws] = []
        try:
            try:
                await self.send_init_from_store(ws)
                await self._finish_initialization(ws)
            except Exception:
                log.exception("send_init_from_store 失败")
                try:
                    await ws.close(code=1011, reason="initialization failed")
                except Exception:
                    log.debug("WebSocket initialization close 失败", exc_info=True)
                return
            # 服务端不做主动 keep-alive ping；由客户端负责发送 ping 帧
            # （handle_client_message 已响应 "ping" → "pong"）。
            # 若客户端长时间无消息，反向代理/OS 可能断开空闲连接，
            # 客户端应自行维护定时 ping 间隔（推荐 30s）。
            while True:
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=300)
                except asyncio.TimeoutError:
                    # 5 分钟无消息 → 僵尸连接，主动关闭
                    log.info("WebSocket 空闲超时（5 分钟），关闭连接")
                    await ws.close(code=1000, reason="idle timeout")
                    break
                except WebSocketDisconnect:
                    break
                except Exception as exc:
                    log.warning(
                        "WebSocket receive_text() 异常，断开连接: %s",
                        exc,
                    )
                    break
                await self.handle_client_message(ws, raw)
        except WebSocketDisconnect:
            pass
        finally:
            self.remove(ws)

    async def handle_client_message(self, ws: WebSocket, raw: str) -> None:
        """Handle lightweight client control messages."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            if raw.strip().lower() == "ping":
                await self._send_control(ws, {"type": "pong"})
                return
            await self._send_control(ws, {"type": "error", "message": "invalid_json"})
            return

        if not isinstance(payload, dict):
            await self._send_control(ws, {"type": "error", "message": "invalid_message"})
            return

        message_type = str(payload.get("type", "")).lower()
        if message_type == "ping":
            await self._send_control(ws, {"type": "pong"})
            return
        if message_type in {"pong", "ack"}:
            return

        await self._send_control(
            ws,
            {
                "type": "error",
                "message": "unsupported_message",
                "received_type": message_type,
            },
        )

    async def _send_control(self, ws: WebSocket, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=_json_serializer)
        try:
            await ws.send_text(data)
        except Exception:
            log.debug("_send_control send_text 失败（连接可能已断开）", exc_info=True)
            self.remove(ws)

    async def _on_event(self, event: Event):
        if not self._active_ws and not self._initializing_ws:
            return
        try:
            data = json.dumps(event.as_dict(), ensure_ascii=False, default=_json_serializer)
        except Exception as e:
            log.warning(
                "WebSocket JSON 序列化失败: %s — %s，对 data 字段做安全降级",
                event.type.value,
                e,
                exc_info=True,
            )
            safe_dict: dict[str, object] = {"type": event.type.value}
            for k, v in event.data.items():
                try:
                    json.dumps(v, ensure_ascii=False, default=_json_serializer)
                    safe_dict[k] = v
                except Exception:
                    safe_dict[k] = repr(v)
            try:
                data = json.dumps(safe_dict, ensure_ascii=False, default=_json_serializer)
            except Exception:
                log.error(
                    "WebSocket JSON 降级序列化仍然失败: %s",
                    event.type.value,
                    exc_info=True,
                )
                data = json.dumps({"type": event.type.value, "error": "serialization_failed"})

        for queued in self._initializing_ws.values():
            queued.append(data)
        _DEAD = b"DEAD"  # sentinel
        _SEND_TIMEOUT = 3.0  # per-client 广播超时

        async def _send(ws: WebSocket):
            client_state = getattr(ws, "client_state", None)
            if (
                isinstance(client_state, WebSocketState)
                and client_state != WebSocketState.CONNECTED
            ):
                return _DEAD
            try:
                await ws.send_text(data)
            except Exception:
                # 连接已断开时由外层统一清理。
                # CancelledError 必须传播，让 TaskGroup 回收整个 fan-out。
                return _DEAD
            return None

        async def _send_with_timeout(ws: WebSocket):
            try:
                return await asyncio.wait_for(_send(ws), timeout=_SEND_TIMEOUT)
            except Exception as exc:
                return exc

        # ── 并发模型说明 ──────────────────────────────
        # _active_ws 是普通的 set[WebSocket]，未使用 asyncio.Lock 保护。
        # 这是安全的，因为：
        # 1. add() / remove() 只在 handle_connection() 协程中调用，
        #    而 handle_connection() 与本方法 (_on_event) 运行在同一个
        #    asyncio 事件循环中。
        # 2. asyncio 协程只在 await 点切换，set 的 add/discard/difference_update
        #    等原子操作之间不会发生竞态。
        # 3. 快照 + difference_update 模式确保：迭代 snapshot 期间即使
        #    handle_connection() 调用了 remove() 修改 _active_ws，
        #    也不会导致 RuntimeError: Set changed size during iteration。
        # ⚠️ 未来若重构为多事件循环或多线程，必须为 _active_ws 加锁。
        # ─────────────────────────────────────────────
        snapshot = list(self._active_ws)
        dead: set[WebSocket] = set()
        try:
            async with asyncio.TaskGroup() as task_group:
                tasks = [task_group.create_task(_send_with_timeout(ws)) for ws in snapshot]
            results = [task.result() for task in tasks]
            for ws, result in zip(snapshot, results):
                if isinstance(result, Exception):
                    if isinstance(result, asyncio.TimeoutError):
                        log.warning(
                            "WebSocket broadcast 单客户端超时（%.1fs），标记为 DEAD",
                            _SEND_TIMEOUT,
                        )
                    else:
                        log.error("WebSocket broadcast 异常: %s", result)
                    dead.add(ws)
                elif result is _DEAD:
                    dead.add(ws)
        finally:
            self._active_ws.difference_update(dead)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    app = getattr(ws, "app", None)
    if app is None:
        log.error("WebSocket 连接被拒绝：ws.app 缺失")
        try:
            await ws.close(code=1011, reason="app not available")
        except Exception:
            log.debug("ws.close 失败（连接可能已断开）", exc_info=True)
        return
    app_state = getattr(app, "state", None)
    if app_state is None:
        log.error("WebSocket 连接被拒绝：app.state 缺失")
        try:
            await ws.close(code=1011, reason="app.state not available")
        except Exception:
            log.debug("ws.close 失败（连接可能已断开）", exc_info=True)
        return
    ctx = getattr(app_state, "ctx", None)
    if ctx is None:
        log.error("WebSocket 连接被拒绝：ctx 缺失")
        try:
            await ws.close(code=1011, reason="context not available")
        except Exception:
            log.debug("ws.close 失败（连接可能已断开）", exc_info=True)
        return
    app_services = getattr(ctx, "app_services", None)
    broadcaster = getattr(app_services, "broadcaster", None)
    if broadcaster:
        await broadcaster.handle_connection(ws)
    else:
        log.error("WebSocket 连接被拒绝：broadcaster 未初始化")
        try:
            await ws.close(code=1011, reason="broadcaster not ready")
        except Exception:
            log.debug("ws.close 失败（连接可能已断开）", exc_info=True)
