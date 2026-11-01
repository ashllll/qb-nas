"""重启/崩溃后卡在「加入中」的条目必须能自己走回来，并且能重试。

现场：点「下载」时条目先进 ``adding``，然后才去调 qB 的 add。这中间只要进程没了
（容器重建/重启、kill -9、部署时重启都在这一档），条目就永远停在 adding：

- qB 同步只对「qB 里能查到的种子」改状态，查不到就不动（``reconcile_snapshot``
  torrent=None 时直接 return），所以它不会自愈；
- 重新点「下载」没用：``UserActionExecutor.download`` 只收 pending/error，adding 被算作
  skipped，界面上什么都看不出来；
- ``reclassify`` 也跳过 adding。

结果：一条记录永久停在「添加中」，无人值守的机器上没人会发现。
"""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.bus import NullBus
from magnet_harvester.context.app_context import (
    AppContext,
    AppServices,
    CoreServices,
    RuntimeState,
)
from magnet_harvester.models import MagnetItem, TaskStatus
from magnet_harvester.store import AsyncItemStore, InMemoryItemStore
from magnet_harvester.transitions import DownloadTransitions

HEX_A = "a" * 40
HEX_B = "b" * 40


def item(hex_key: str, status: TaskStatus) -> MagnetItem:
    return MagnetItem(
        hash=hex_key,
        name=f"Item.{hex_key[:6]}",
        magnet=f"magnet:?xt=urn:btih:{hex_key}",
        category="电影",
        status=status,
    )


def build():
    store = AsyncItemStore(InMemoryItemStore())
    downloads = DownloadTransitions(store=store, bus=NullBus())
    return store, downloads


@pytest.mark.asyncio
async def test_interrupted_adding_is_marked_with_a_reason():
    store, downloads = build()
    await store.add(item(HEX_A, TaskStatus.adding))
    await store.add(item(HEX_B, TaskStatus.downloading))

    recovered = await downloads.recover_interrupted()

    assert recovered == 1
    first = await store.get(HEX_A)
    assert first.status == TaskStatus.error, "卡在 adding 的条目必须退回可重试状态"
    assert first.error_msg and ("中断" in first.error_msg or "未知" in first.error_msg)
    second = await store.get(HEX_B)
    assert second.status == TaskStatus.downloading, "别的条目不许被动到"


@pytest.mark.asyncio
async def test_recovered_item_can_be_downloaded_again():
    """恢复之后重新点「下载」要能受理（error 是可提交状态）。"""
    store, downloads = build()
    await store.add(item(HEX_A, TaskStatus.adding))
    await downloads.recover_interrupted()

    assert await downloads.submitting(HEX_A) is True
    assert (await store.get(HEX_A)).status == TaskStatus.adding


def test_runtime_start_recovers_interrupted_additions():
    """服务启动时就会做这件事，不用人来点。"""
    from magnet_harvester.assembly import AppRuntime

    calls: list[str] = []

    class Pipeline:
        async def recover_interrupted(self):
            calls.append("recover")
            return 0

    class Crawler:
        async def start(self):
            calls.append("crawler")

    class SyncLoop:
        async def start(self):
            calls.append("sync")

    ctx = AppContext(
        core=CoreServices(
            store=None,
            bus=None,
            pipeline=Pipeline(),
            crawler=Crawler(),
            classifier=None,
            qbit=None,
        ),
        runtime=RuntimeState(),
        app_services=AppServices(ingest=None),
    )

    asyncio.run(AppRuntime(ctx=ctx, sync_loop=SyncLoop()).start())

    assert calls == ["crawler", "recover", "sync"], calls
