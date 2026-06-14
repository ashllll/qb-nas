"""
ItemEventEmitter — shared event emission for Magnet item state changes.

Used by both MagnetItemTransitions (pipeline) and QBitSyncLoop (services)
to ensure a single, authoritative rule for when STORE_CHANGED and
DOWNLOAD_RESULT events are emitted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from magnet_harvester.bus import Event, EventType
from magnet_harvester.models import TaskStatus
from magnet_harvester.utils.serializers import _item_payload

if TYPE_CHECKING:
    from magnet_harvester.bus import MessageBus
    from magnet_harvester.store import ItemStore


class ItemEventEmitter:
    """Shared event emission rules for Magnet item state transitions.

    - STORE_CHANGED is always emitted when an item changes.
    - DOWNLOAD_RESULT is only emitted on terminal (success/error) or
      new-phase transitions (from pending/adding/classifying/None).
      Routine queued↔downloading oscillations are suppressed to avoid
      log noise and UI flicker.
    """

    def __init__(self, store: ItemStore, bus: MessageBus):
        self._store = store
        self._bus = bus

    async def emit_item_changed(self, hash_key: str) -> None:
        """Emit STORE_CHANGED for the item at hash_key (no-op if missing)."""
        item = self._store.get(hash_key)
        if item is not None:
            await self._bus.emit(
                Event(EventType.STORE_CHANGED, {"item": _item_payload(item)})
            )

    async def emit_download_result(
        self, hash_key: str, previous_status: TaskStatus | None = None
    ) -> None:
        """Emit DOWNLOAD_RESULT only on terminal or new-phase transitions.

        Suppressed for routine queued↔downloading oscillations to avoid
        flooding the WebSocket with noise.
        """
        item = self._store.get(hash_key)
        if item is None:
            return

        is_terminal = item.status in {TaskStatus.success, TaskStatus.error}
        is_new_phase = previous_status in {
            TaskStatus.pending, TaskStatus.adding, TaskStatus.classifying, None,
        }

        if is_terminal or is_new_phase:
            await self._bus.emit(
                Event(
                    EventType.DOWNLOAD_RESULT,
                    {
                        "hash": hash_key,
                        "status": item.status.value,
                        "error_msg": item.error_msg,
                        "progress": item.progress,
                        "torrent_state": item.torrent_state,
                    },
                )
            )
