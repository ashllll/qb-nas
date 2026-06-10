"""
Application context — dependency container for Magnet Harvester.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from magnet_harvester.store import ItemStore
    from magnet_harvester.bus import MessageBus
    from magnet_harvester.pipeline import HarvestPipeline
    from magnet_harvester.crawler import MagnetCrawler
    from magnet_harvester.classifier import LocalClassifier
    from magnet_harvester.qbit_client import QBittorrentClient


@dataclass
class AppContext:
    store: ItemStore
    bus: MessageBus
    pipeline: HarvestPipeline
    crawler: MagnetCrawler
    classifier: LocalClassifier
    qbit: QBittorrentClient
    stats: Any = None
    bg_manager: Any = None
    broadcaster: Any = None
    tool_executor: Any = None
    qbit_lock: Any = None


@dataclass
class RuntimeContext:
    ctx: AppContext

    async def replace_qbit(self, new_qbit):
        old_qbit = self.ctx.qbit
        self.ctx.qbit = new_qbit
        self.ctx.pipeline.replace_download_phase(new_qbit)
        if old_qbit is not None:
            await old_qbit.close()


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx
