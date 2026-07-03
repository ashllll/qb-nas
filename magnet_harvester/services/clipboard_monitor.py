"""
ClipboardMonitor — 系统剪贴板监控，检测 magnet 链接自动处理。

通过 pyperclip 轮询 Windows 剪贴板，提取磁力链接后走分类→存储→下载流程。
通过 WebSocket 向 UI 推送状态和磁力发现事件。

磁力解析复用 magnet_parser.extract_from_text，支持标准 magnet、URL/HTML
解码后的 magnet、JSON/引号包裹的 magnet 以及 Base64 编码的 magnet；剪贴板
 ingestion 不应用爬虫的分辨率过滤策略。
"""

from __future__ import annotations

import asyncio
import logging

import pyperclip

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.context.app_context import UserActionExecutorLike
from magnet_harvester.transitions import MagnetItemTransitions
from magnet_harvester.magnet_sources import MagnetSourceExtractor
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.pipeline import HarvestPipeline
from magnet_harvester.store import ItemStore
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)


class ClipboardMonitor:
    """轮询系统剪贴板，检测新磁力链接自动分类并下载。"""

    MAX_CONSECUTIVE_FAILURES: int = 10

    def __init__(
        self,
        bus: MessageBus,
        store: ItemStore,
        classifier: LocalClassifier,
        pipeline: "HarvestPipeline | None" = None,
        action_executor: UserActionExecutorLike | None = None,
        poll_interval: float = 1.0,
        transitions: MagnetItemTransitions | None = None,
        task_manager: BGTaskManager | None = None,
    ):
        self._bus = bus
        self._store = store
        self._classifier = classifier
        self._pipeline = pipeline
        self._action_executor = action_executor
        self._task_manager = task_manager
        self._transitions = transitions or MagnetItemTransitions(store=store, bus=bus)
        self._magnet_sources = MagnetSourceExtractor()
        self._poll_interval = poll_interval
        self._running = False
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_seen: str | None = None
        self._magnet_count: int = 0
        self._consecutive_failures: int = 0
        self._total_failure_cycles: int = 0
        self._max_failure_cycles: int = 5

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def magnet_count(self) -> int:
        return self._magnet_count

    async def start(self):
        """启动剪贴板监控。"""
        async with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._task = BGTaskManager.spawn(
                self._run(), task_manager=self._task_manager, name="clipboard-monitor"
            )
            await self._bus.emit(
                Event(
                    EventType.CLIPBOARD_STATUS,
                    {
                        "running": True,
                        "magnet_count": self._magnet_count,
                    },
                )
            )
        log.info("剪贴板监控已启动")

    async def stop(self):
        """停止剪贴板监控。"""
        async with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            if self._task:
                self._task.cancel()
                try:
                    await asyncio.wait_for(self._task, timeout=5.0)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    log.warning("剪贴板监控任务未能在 5 秒内取消，继续关闭流程")
                self._task = None
        # bus.emit 移到锁外执行，避免持锁期间阻塞 start()/shutdown()
        await self._bus.emit(
            Event(
                EventType.CLIPBOARD_STATUS,
                {
                    "running": False,
                    "magnet_count": self._magnet_count,
                },
            )
        )
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
                self._consecutive_failures = 0
            except Exception as e:
                self._consecutive_failures += 1
                log.warning(f"剪贴板读取异常（连续 {self._consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES}）: {e}")
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self._consecutive_failures = 0
                    self._total_failure_cycles += 1
                    if self._total_failure_cycles >= self._max_failure_cycles:
                        log.error(
                            "剪贴板读取连续失败 %d 个周期，自动停止监控",
                            self._total_failure_cycles,
                        )
                        async with self._lock:
                            self._running = False
                        await self._bus.emit(Event(EventType.CLIPBOARD_STATUS, {"running": False}))
                        break
                    log.error(
                        "剪贴板读取连续失败，休眠 30 秒后重试（第 %d/%d 个周期）",
                        self._total_failure_cycles,
                        self._max_failure_cycles,
                    )
                    await asyncio.sleep(30)
                content = None

            if content and isinstance(content, str) and content != self._last_seen:
                for item in self._magnet_sources.from_clipboard_text(content):
                    if not self._running:
                        break
                    try:
                        await self._handle_item(item)
                    except Exception as e:
                        log.error(f"剪贴板条目处理失败: {e}", exc_info=True)
                else:
                    # 只在所有 item 处理完后才标记已处理，避免中途 stop 导致剩余 magnet 永久丢失
                    self._last_seen = content

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
                break
            except asyncio.TimeoutError:
                pass

    async def _handle_item(self, item: dict):
        """处理单个已解析的磁力条目：分类、存储、发布事件。"""
        name = item["name"]

        # 分类
        result = self._classifier.classify_one(name)
        category = result.get("category", "其他") or "其他"
        save_path = result.get("save_path", category) or category

        # 构建 MagnetItem
        magnet_item = MagnetItem(
            hash=item["hash"],
            name=name,
            magnet=item["magnet"],
            category=category,
            save_path=save_path,
            status=TaskStatus.pending,
            source_url="clipboard://",
            size=item.get("size"),
        )

        # 存储（去重：已存在则跳过）并发布事件
        if not await self._transitions.clipboard_found(magnet_item):
            log.debug(f"剪贴板磁力已存在，跳过: {name[:40]}")
            return

        self._magnet_count += 1

        log.info(f"剪贴板捕获磁力: {name[:50]} → {category}")

        # 自动发送到 qBittorrent（通过 action_executor 统一入口，确保 stats 计数和未来保护措施生效）
        if self._action_executor:
            result = await self._action_executor.download([magnet_item.hash], task_name="clipboard_download")
            if result.get("status") == "started":
                log.info(f"剪贴板自动下载: {name[:40]}")
            else:
                log.warning("剪贴板自动下载失败: %s, 原因: %s", name[:40], result.get("reason", "未知"))
        elif self._pipeline:
            # 向后兼容：未注入 action_executor 时回退到 pipeline
            await self._pipeline.download([magnet_item.hash])
            log.info(f"剪贴板自动下载: {name[:40]}")
        else:
            log.warning("剪贴板捕获了磁力但 action_executor 和 pipeline 均为 None，无法自动下载: %s", name[:50])
