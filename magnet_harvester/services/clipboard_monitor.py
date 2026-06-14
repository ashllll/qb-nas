"""
ClipboardMonitor — 系统剪贴板监控，检测 magnet 链接自动处理。

通过 pyperclip 轮询 Windows 剪贴板，提取磁力链接后走分类→存储→下载流程。
通过 WebSocket 向 UI 推送状态和磁力发现事件。
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import unquote

import pyperclip

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import ItemStore
from magnet_harvester.utils.serializers import _item_payload

log = logging.getLogger(__name__)

_MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[A-Za-z0-9]+[^\s]*", re.IGNORECASE)
_DN_RE = re.compile(r"dn=([^&]+)", re.IGNORECASE)
_BTIH_RE = re.compile(r"btih:([A-Za-z0-9]+)", re.IGNORECASE)


def extract_btih(magnet: str) -> str:
    """从磁力链接提取 info hash（btih 值）。"""
    m = _BTIH_RE.search(magnet)
    return m.group(1).lower() if m else magnet


def extract_display_name(magnet: str) -> str:
    """从磁力链接提取显示名称（dn= 参数）。"""
    m = _DN_RE.search(magnet)
    if m:
        try:
            return unquote(m.group(1)).strip()
        except Exception:
            pass
    return magnet[:60]


class ClipboardMonitor:
    """轮询系统剪贴板，检测新磁力链接并通过 MessageBus 发布事件。"""

    def __init__(
        self,
        bus: MessageBus,
        store: ItemStore,
        classifier: LocalClassifier,
        poll_interval: float = 1.0,
    ):
        self._bus = bus
        self._store = store
        self._classifier = classifier
        self._poll_interval = poll_interval
        self._running = False
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_seen: str | None = None
        self._magnet_count: int = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def magnet_count(self) -> int:
        return self._magnet_count

    async def start(self):
        """启动剪贴板监控。"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="clipboard-monitor")
        await self._bus.emit(Event(EventType.CLIPBOARD_STATUS, {
            "running": True,
            "magnet_count": self._magnet_count,
        }))
        log.info("剪贴板监控已启动")

    async def stop(self):
        """停止剪贴板监控。"""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._bus.emit(Event(EventType.CLIPBOARD_STATUS, {
            "running": False,
            "magnet_count": self._magnet_count,
        }))
        log.info("剪贴板监控已停止")

    async def shutdown(self):
        """服务关闭时清理。"""
        if self._running:
            await self.stop()

    async def _run(self):
        """主循环：轮询剪贴板内容。"""
        while not self._stop_event.is_set():
            try:
                content = await asyncio.to_thread(pyperclip.paste)
                if content and isinstance(content, str) and content != self._last_seen:
                    self._last_seen = content
                    magnets = _MAGNET_RE.findall(content)
                    for magnet in magnets:
                        if not self._running:
                            break
                        await self._handle_magnet(magnet.strip())
            except Exception as e:
                log.debug(f"剪贴板读取异常: {e}")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
                break
            except asyncio.TimeoutError:
                pass

    async def _handle_magnet(self, magnet: str):
        """处理单个磁力链接：分类、存储、发布事件。"""
        name = extract_display_name(magnet)

        # 分类
        result = self._classifier.classify_one(name)
        category = result.get("category", "其他") or "其他"
        save_path = result.get("save_path", category) or category

        # 构建 MagnetItem
        btih = extract_btih(magnet)
        item = MagnetItem(
            hash=btih,
            name=name,
            magnet=magnet,
            category=category,
            save_path=save_path,
            status=TaskStatus.pending,
            source_url="clipboard://",
        )

        # 存储（去重：已存在则跳过）
        if not self._store.add(item):
            log.debug(f"剪贴板磁力已存在，跳过: {name[:40]}")
            return

        self._magnet_count += 1

        # 发布事件
        await self._bus.emit(Event(EventType.MAGNET_FOUND, {
            "item": item.model_dump(),
        }))
        await self._bus.emit(Event(EventType.STORE_CHANGED, {
            "item": _item_payload(item),
        }))
        log.info(f"剪贴板捕获磁力: {name[:50]} → {category}")
