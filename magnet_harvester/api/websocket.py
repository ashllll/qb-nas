"""
WebSocket broadcaster — manages active connections and broadcasts events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from magnet_harvester.bus import Event, MessageBus
from magnet_harvester.store import ItemStore
from magnet_harvester.utils.auth import resolve_auth
from magnet_harvester.utils.bg_tasks import BGTaskManager
from magnet_harvester.utils.serializers import item_payload

log = logging.getLogger(__name__)
router = APIRouter()

_INIT_PAGE_SIZE = 500
# 单个在线连接的发送积压上限：超过说明客户端读得比事件产出慢，直接断开重连。
_CLIENT_QUEUE_MAX = 32
# 初始化快照期间的积压上限（快照本身可能分多页，事件先攒后发）。
_INIT_QUEUE_MAX = 1000
# 单条消息发送超时：慢客户端不能无限占住自己的发送协程。
_SEND_TIMEOUT = 3.0
_CLOSE_SLOW = 1013


@dataclass
class _Client:
    ws: WebSocket
    queue: asyncio.Queue[str]
    limit: int
    sender: asyncio.Task | None = None


def _json_serializer(obj: Any) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class WSBroadcaster:
    """Subscribes to MessageBus and broadcasts events to all active WebSocket clients."""

    def __init__(self, bus: MessageBus, store: ItemStore | None = None):
        self._bus = bus
        self._store = store
        self._clients: dict[WebSocket, _Client] = {}
        self._active_ws: set[WebSocket] = set()
        bus.subscribe(None, self._on_event)

    def add(self, ws: WebSocket):
        """把已建好的连接登记为在线客户端，并启动独立发送协程。"""
        if ws in self._clients:
            return
        client = _Client(ws=ws, queue=asyncio.Queue(), limit=_CLIENT_QUEUE_MAX)
        self._clients[ws] = client
        self._active_ws.add(ws)
        client.sender = BGTaskManager.spawn(self._sender_loop(client), name="ws-sender")

    def _register_initializing(self, ws: WebSocket) -> None:
        client = _Client(ws=ws, queue=asyncio.Queue(), limit=_INIT_QUEUE_MAX)
        previous = self._clients.pop(ws, None)
        if previous is not None and previous.sender is not None:
            previous.sender.cancel()
        self._active_ws.discard(ws)
        self._clients[ws] = client

    def remove(self, ws: WebSocket):
        client = self._clients.pop(ws, None)
        self._active_ws.discard(ws)
        if client is not None and client.sender is not None:
            client.sender.cancel()

    def shutdown(self):
        """取消 MessageBus 订阅并停止所有发送协程。"""
        self._bus.unsubscribe(None, self._on_event)
        for ws in list(self._clients):
            self.remove(ws)

    @property
    def active_count(self) -> int:
        return len(self._active_ws)

    async def _send_text(self, ws: WebSocket, data: str) -> None:
        await asyncio.wait_for(ws.send_text(data), timeout=_SEND_TIMEOUT)

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
        client = self._clients.get(ws)
        if client is None:
            return
        client.limit = _CLIENT_QUEUE_MAX
        self._active_ws.add(ws)
        # 快照期间攒下的事件都在队列里，独立 sender 按 FIFO 发送，天然排在 init_done 之后。
        client.sender = BGTaskManager.spawn(self._sender_loop(client), name="ws-sender")

    async def _sender_loop(self, client: _Client) -> None:
        failed = False
        try:
            while True:
                data = await client.queue.get()
                try:
                    state = getattr(client.ws, "client_state", None)
                    if isinstance(state, WebSocketState) and state != WebSocketState.CONNECTED:
                        failed = True
                        break
                    await asyncio.wait_for(client.ws.send_text(data), timeout=_SEND_TIMEOUT)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("WebSocket 发送失败，断开连接: %s", exc)
                    failed = True
                    break
                finally:
                    client.queue.task_done()
        finally:
            self._detach(client)
            if failed:
                await self._close_ws(client.ws, _CLOSE_SLOW, "send failed")

    def _detach(self, client: _Client) -> None:
        if self._clients.get(client.ws) is client:
            self._clients.pop(client.ws, None)
            self._active_ws.discard(client.ws)

    def _drop_client(self, client: _Client, reason: str) -> None:
        if self._clients.get(client.ws) is not client:
            return
        self._detach(client)
        if client.sender is not None:
            client.sender.cancel()
        BGTaskManager.spawn(self._close_ws(client.ws, _CLOSE_SLOW, reason), name="ws-close-slow")

    @staticmethod
    async def _close_ws(ws: WebSocket, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            log.debug("WebSocket close 失败（连接可能已断开）", exc_info=True)

    async def handle_connection(self, ws: WebSocket, already_accepted: bool = False):
        """Full WebSocket lifecycle: accept, init, keep-alive, cleanup.

        ``already_accepted``：端点需要先 accept 才能把鉴权失败的原因（4401 +
        reason）送达到浏览器；重复 accept 会抛错，所以这里要认这个标记。
        """
        if not already_accepted:
            await ws.accept()
        self._register_initializing(ws)
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
        if message_type == "auth":
            # 握手阶段已校验；这里只确认，不回显、不记录 key
            await self._send_control(ws, {"type": "auth_ok"})
            return
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
        if not self._clients:
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

        # 回调只入队，绝不在这里 await 网络发送：慢客户端不能反压 bus/qB 同步。
        for client in list(self._clients.values()):
            if client.queue.qsize() >= client.limit:
                log.warning(
                    "WebSocket 客户端发送积压达到上限（%d），断开连接",
                    client.limit,
                )
                self._drop_client(client, "client too slow")
                continue
            client.queue.put_nowait(data)


_AUTH_TIMEOUT_SECONDS = 8.0


def _key_matches(supplied: str, expected: str) -> bool:
    """常量时间比较；非 ASCII 一律按失败处理（与 REST 一致）。"""
    try:
        return bool(supplied) and secrets.compare_digest(supplied, expected)
    except TypeError:
        return False


async def _first_frame_api_key(ws: WebSocket) -> tuple[str, str]:
    """等第一帧 ``{"type":"auth","api_key":...}``；超时/非法返回 ("", "")。"""
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=_AUTH_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        return "", ""
    except Exception:
        log.debug("等待 WebSocket 认证帧失败", exc_info=True)
        return "", ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    if str(payload.get("type", "")).lower() != "auth":
        return "", ""
    return str(payload.get("api_key") or "").strip(), "auth"


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

    # ── 授权（与写接口共用 utils/auth.resolve_auth） ─────────
    # trusted（默认）：局域网页面直连，不需要 key；strict：第一帧带
    # {"type":"auth","api_key":...}。key 不进 URL/查询串/日志。
    # Host/Origin 已由入口中间件挡过一次，这里再判一次是防御纵深。
    auth = resolve_auth(ws)
    if not auth.host_ok or not auth.origin_ok:
        log.warning("WebSocket 连接被拒绝：%s", auth.reason)
        try:
            await ws.close(code=4403, reason=auth.reason)
        except Exception:
            log.debug("ws.close 失败（连接可能已断开）", exc_info=True)
        return

    expected = (getattr(getattr(ctx, "runtime", None), "api_key", "") or "").strip()
    # 必须先 accept：握手阶段直接 close 会被浏览器当成 1006/403，
    # 界面就分不清「没带 key」「key 不对」「服务端没配 key」。
    await ws.accept()
    if auth.required and not auth.trusted:
        frame_key, _frame_type = await _first_frame_api_key(ws)
        # 是否"带过 key"决定原因是 api_key_invalid 还是 api_key_missing
        supplied_any = bool(frame_key)
        if not _key_matches(frame_key, expected):
            reason = "api_key_invalid" if supplied_any else "api_key_missing"
            # 只记原因，绝不记 key
            log.warning("WebSocket 鉴权失败：%s", reason)
            try:
                await ws.close(code=4401, reason=reason)
            except Exception:
                log.debug("ws.close 失败（连接可能已断开）", exc_info=True)
            return
    # 明确告诉界面这个连接要不要 key（不含凭据）；trusted 下即使服务端配了
    # API_KEY，页面也不需要它，所以 auth_required 跟随 auth.required。
    try:
        await ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "auth_required": auth.required,
                    "mode": auth.mode,
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        log.debug("hello 发送失败（连接可能已断开）", exc_info=True)
        return
    app_services = getattr(ctx, "app_services", None)
    broadcaster = getattr(app_services, "broadcaster", None)
    if broadcaster:
        await broadcaster.handle_connection(ws, already_accepted=True)
    else:
        log.error("WebSocket 连接被拒绝：broadcaster 未初始化")
        try:
            await ws.close(code=1011, reason="broadcaster not ready")
        except Exception:
            log.debug("ws.close 失败（连接可能已断开）", exc_info=True)
