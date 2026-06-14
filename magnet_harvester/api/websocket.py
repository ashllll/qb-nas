"""
WebSocket broadcaster — manages active connections and broadcasts events.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from magnet_harvester.bus import Event, MessageBus
from magnet_harvester.utils.serializers import item_payload

log = logging.getLogger(__name__)
router = APIRouter()


def _json_serializer(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class BroadcasterStore(Protocol):
    def list(self, limit: int = 20): ...


class WSBroadcaster:
    """Subscribes to MessageBus and broadcasts events to all active WebSocket clients."""

    def __init__(self, bus: MessageBus, store: BroadcasterStore | None = None):
        self._bus = bus
        self._store = store
        self._active_ws: set[WebSocket] = set()
        bus.subscribe(None, self._on_event)

    def add(self, ws: WebSocket):
        self._active_ws.add(ws)

    def remove(self, ws: WebSocket):
        self._active_ws.discard(ws)

    @property
    def active_count(self) -> int:
        return len(self._active_ws)

    async def send_init(self, ws: WebSocket, items: list):
        data = json.dumps({"type": "init", "items": items}, ensure_ascii=False, default=_json_serializer)
        await ws.send_text(data)

    async def send_init_from_store(self, ws: WebSocket):
        if self._store:
            items = [item_payload(i) for i in self._store.list(limit=10000)]
            await self.send_init(ws, items)
        else:
            await self.send_init(ws, [])

    async def handle_connection(self, ws: WebSocket):
        """Full WebSocket lifecycle: accept, init, keep-alive, cleanup."""
        await ws.accept()
        self.add(ws)
        try:
            await self.send_init_from_store(ws)
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            self.remove(ws)

    async def _on_event(self, event: Event):
        if not self._active_ws:
            return
        try:
            data = json.dumps(event.as_dict(), ensure_ascii=False, default=_json_serializer)
        except Exception:
            log.warning("WebSocket JSON 序列化失败: %s", event.type.value, exc_info=True)
            return
        dead = set()

        async def _send(ws: WebSocket):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)

        await asyncio.gather(*[_send(ws) for ws in self._active_ws], return_exceptions=True)
        self._active_ws.difference_update(dead)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    ctx = getattr(getattr(ws, "app", None), "state", None)
    broadcaster = getattr(getattr(ctx, "ctx", None), "broadcaster", None)
    if broadcaster:
        await broadcaster.handle_connection(ws)
