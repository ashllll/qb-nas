"""
WebSocket broadcaster — manages active connections and broadcasts events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from magnet_harvester.bus import Event, MessageBus
from magnet_harvester.store import ItemStore
from magnet_harvester.utils.interface_guard import request_arrived_on_non_loopback_interface
from magnet_harvester.utils.serializers import item_payload

log = logging.getLogger(__name__)
router = APIRouter()


def _insecure_writes_allowed(ctx) -> bool:
    """是否显式允许无鉴权写操作（开发豁免）。"""
    runtime = getattr(ctx, "runtime", None)
    return bool(getattr(runtime, "allow_insecure_write_api", False))


_INIT_PAGE_SIZE = 500

# 单次 send_text 上限：慢客户端不得无限占用初始化/广播路径
_SEND_TIMEOUT = 3.0
# 关闭不可用连接的等待上限
_CLOSE_TIMEOUT = 1.0
# 初始化阶段的背压上限：客户端不读时不得让队列无限增长
_MAX_INIT_QUEUE_ITEMS = 1000
_MAX_INIT_QUEUE_BYTES = 4 * 1024 * 1024


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

    async def _send_text(self, ws: WebSocket, data: str) -> None:
        """发送受 _SEND_TIMEOUT 约束，避免不读数据的客户端卡死整条路径。"""
        await asyncio.wait_for(ws.send_text(data), timeout=_SEND_TIMEOUT)

    async def _close_ws(self, ws: WebSocket, *, code: int, reason: str) -> None:
        """关闭连接，成功后才解除登记。

        只从集合中移除而不关闭会让客户端收不到 onclose：它仍会定期 ping、
        服务端也照常回 pong，于是空闲超时永不触发，连接既不投递事件也永不断开
        （前端仅在 onclose 时重连），表现为 UI 永久静默直到手动刷新。

        因此关闭失败时保留登记，让下一次广播继续重试，而不是留下一个
        既不投递也无人再处理的连接。
        """
        try:
            await asyncio.wait_for(ws.close(code=code, reason=reason), timeout=_CLOSE_TIMEOUT)
        except Exception:
            log.warning("WebSocket 关闭失败，保留登记以便重试", exc_info=True)
            return
        self.remove(ws)

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
        await self._send_text(ws, data)

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
                await self._send_text(
                    ws,
                    json.dumps(
                        {"type": "init_page", "items": payloads},
                        ensure_ascii=False,
                        default=_json_serializer,
                    ),
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
        await self._send_text(
            ws,
            json.dumps(
                {"type": "init_done", "total": total},
                ensure_ascii=False,
            ),
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
                    await self._send_text(ws, data)
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
            await self._send_text(ws, data)
        except Exception:
            log.debug("_send_control send_text 失败，关闭连接", exc_info=True)
            await self._close_ws(ws, code=1011, reason="control send failed")

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

        # 初始化阶段的背压：客户端不读时不得让队列无限增长。
        # 先快照再关闭，避免迭代中修改 _initializing_ws。
        stalled: list[WebSocket] = []
        queues: list[list[str]] = []
        for ws, queued in list(self._initializing_ws.items()):
            queued_bytes = sum(len(entry) for entry in queued)
            if (
                len(queued) + 1 > _MAX_INIT_QUEUE_ITEMS
                or queued_bytes + len(data) > _MAX_INIT_QUEUE_BYTES
            ):
                stalled.append(ws)
            else:
                queues.append(queued)
        for queued in queues:
            queued.append(data)
        for ws in stalled:
            log.warning(
                "WebSocket 初始化阶段积压超限（>%d 条或 >%d 字节），关闭连接",
                _MAX_INIT_QUEUE_ITEMS,
                _MAX_INIT_QUEUE_BYTES,
            )
            await self._close_ws(ws, code=1013, reason="initialization backlog exceeded")
        _DEAD = b"DEAD"  # sentinel

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
            if dead:
                # 必须真正关闭：否则客户端收不到 onclose（仍在 ping、仍收到 pong），
                # 连接既不投递事件也永不断开，前端不会重连。
                # 关闭失败的连接由 _close_ws 保留登记，下一次广播继续重试 ——
                # 此处不能无条件 difference_update，否则会抹掉该重试语义。
                await asyncio.gather(
                    *(
                        self._close_ws(ws, code=1011, reason="send failed or timed out")
                        for ws in dead
                    )
                )


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

    # ── API Key 认证（与写接口一致的策略） ──────────────────
    # API_KEY 为空时保持向后兼容（本地回环部署默认不校验）；
    # 非空时必须携带匹配的 api_key 查询参数 —— 浏览器 WebSocket
    # API 无法自定义请求头，因此走 query param（见 static/app.js）。
    expected_key = getattr(getattr(ctx, "runtime", None), "api_key", "") or ""
    # 未配置 API_KEY 时的兜底：经非 loopback 接口进入的订阅同样拒绝。
    # 与 HTTP 中间件同源判定（ASGI scope 的 server = 该连接被接受的本机接口地址），
    # 因此与启动方式无关，本机 loopback 使用不受影响。
    if not expected_key.strip() and not _insecure_writes_allowed(ctx):
        if request_arrived_on_non_loopback_interface(ws.scope):
            log.warning("拒绝经非 loopback 接口进入的无鉴权 WebSocket 连接")
            try:
                await ws.close(code=4403, reason="non-loopback access without API key")
            except Exception:
                log.debug("ws.close 失败（连接可能已断开）", exc_info=True)
            return
    # 与 REST（utils/auth.py）一致：strip 后为空视为未配置认证（兼容模式）
    if expected_key.strip():
        # 与 REST（utils/auth.py）一致：两侧 strip 后比较
        supplied = ws.query_params.get("api_key", "").strip()
        expected = expected_key.strip()
        # compare_digest 对非 ASCII 字符串会抛 TypeError，统一按认证失败处理
        try:
            authorized = supplied and secrets.compare_digest(supplied, expected)
        except TypeError:
            authorized = False
        if not authorized:
            log.warning("WebSocket 连接因缺少或错误的 API Key 被拒绝")
            try:
                await ws.close(code=4401, reason="unauthorized")
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
