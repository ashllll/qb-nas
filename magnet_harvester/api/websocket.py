"""
WebSocket broadcaster — manages active connections and broadcasts events.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from magnet_harvester.bus import Event, MessageBus
from magnet_harvester.utils.serializers import _item_payload

log = logging.getLogger(__name__)
router = APIRouter()


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
        data = json.dumps({"type": "init", "items": items}, ensure_ascii=False)
        await ws.send_text(data)

    async def send_init_from_store(self, ws: WebSocket):
        if self._store:
            items = [_item_payload(i) for i in self._store.list(limit=10000)]
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
        data = json.dumps(event.as_dict(), ensure_ascii=False)
        dead = set()
        for ws in self._active_ws:
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        self._active_ws.difference_update(dead)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    ctx = getattr(getattr(ws, "app", None), "state", None)
    broadcaster = getattr(getattr(ctx, "ctx", None), "broadcaster", None)
    if broadcaster:
        await broadcaster.handle_connection(ws)
