#!/usr/bin/env python3
"""生产链路 smoke 验证（可选，不参与常规 pytest 门禁）。

默认只检查 Aria2 桥接/飞牛下载中心连通、真实页面抓取和本地分类。
SMOKE_SUBMIT=1 时提交一个占位 magnet 并按持久化 GID 轮询；任务保留在
下载中心，请在验证后手动删除 smoke_test 任务。此检查不证明真实下载落盘。

环境变量：
    SMOKE_CRAWL_URL         必填：真实页面 URL
    SMOKE_ARIA2_RPC_URL     必填：http://NAS:6800/jsonrpc
    SMOKE_ARIA2_SECRET      必填：桥接 RPC 密钥
    SMOKE_ARIA2_SAVE_PATH   可选：NAS 下载根目录，空则使用桥接默认目录
    SMOKE_STATE_DB          可选：测试任务绑定库，默认 data/smoke-aria2.db
    SMOKE_SITE_COOKIES      可选：JSON 格式站点 Cookie
    SMOKE_SUBMIT            设为 1 才向下载中心创建测试任务

退出码：全部步骤 PASS 为 0，任一 FAIL 为 1。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.local_classifier import LocalClassificationEngine
from magnet_harvester.config import Aria2Config, CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.aria2_client import Aria2BridgeClient
from magnet_harvester.services.site_auth import SiteAuth

# 提交链路使用的 magnet：hash 为占位值（格式合法但无真实对等体），
# 仅验证 add_magnet 提交与状态轮询机制；如需真实下载请替换为有效 magnet。
_SMOKE_HASH = "0123456789abcdef0123456789abcdef01234567"
_PLACEHOLDER_MAGNET = f"magnet:?xt=urn:btih:{_SMOKE_HASH}&dn=smoke-test-placeholder"

# 步骤结果容器
_STEPS: list[tuple[str, bool, str]] = []


def _record(step: str, ok: bool, detail: str) -> None:
    _STEPS.append((step, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {step}: {detail}")


async def _check_bridge(
    rpc_url: str, secret: str, save_path: str, state_db: Path, submit: bool
) -> None:
    """用实际适配器检查下游；占位任务只按哈希/GID 对应关系认领。"""
    client = Aria2BridgeClient(
        config=Aria2Config(
            rpc_url=rpc_url, secret=secret, save_path=save_path, state_db_path=str(state_db)
        )
    )
    try:
        if not await client.ping():
            _record("下载中心连通", False, "检查 RPC 地址/密钥及桥接中的飞牛登录状态")
            return
        _record("下载中心连通", True, "桥接与下载中心只读查询通过")
        if not submit:
            _record("提交链路", True, "跳过（SMOKE_SUBMIT=1 时创建测试任务）")
            return
        if not await client.add_magnet(
            _PLACEHOLDER_MAGNET, category="smoke_test", save_path="smoke_test"
        ):
            _record("提交链路", False, "提交未确认；先检查下载中心，勿盲目重复提交")
            return
        _record("提交链路", True, "已提交；任务保留在下载中心，验证后请手动清理 smoke_test")
        for _ in range(10):
            await asyncio.sleep(2)
            snapshot, removed = await client.poll_torrent_snapshot_with_removed()
            torrent = snapshot.get(_SMOKE_HASH)
            if torrent:
                state = torrent["download_state"]["torrent_state"]
                _record("提交轮询", True, f"已观察到测试任务：{state}")
                return
            if _SMOKE_HASH in removed:
                break
        _record("提交轮询", False, "未观察到测试任务；请检查下载中心与桥接日志")
    except Exception as exc:
        _record("下载中心链路", False, f"{type(exc).__name__}（检查桥接日志）")
    finally:
        await client.close()


async def _check_crawl(url: str, cookies_raw: str | None) -> None:
    """真实站点抓取 + 本地分类。"""
    try:
        timeout = int(os.environ.get("SMOKE_CRAWL_TIMEOUT", "60").strip())
    except ValueError:
        timeout = 60
    crawler = MagnetCrawler(
        config=CrawlerConfig(max_depth=1, timeout=timeout),
        site_auth=SiteAuth.from_raw(cookies_raw) if cookies_raw else None,
    )
    await crawler.start()
    found_items: list[dict] = []
    try:
        async for msg in crawler.crawl(url, depth=1):
            mtype = msg.get("type")
            if mtype == "found" and msg.get("item"):
                found_items.append(msg["item"])
            elif mtype == "error":
                print(f"   [爬虫] 页面错误: {msg.get('msg')}")
            elif mtype == "done":
                break
        if not found_items:
            _record("站点抓取", False, f"{url} 未提取到任何 magnet（页面无磁力链接或加载失败）")
            return
        _record("站点抓取", True, f"{url} 提取到 {len(found_items)} 个磁力链接")

        engine = LocalClassificationEngine()
        categories = {engine.classify_name(i.get("name", ""))["category"] for i in found_items}
        _record(
            "本地分类",
            True,
            f"分类结果: {', '.join(sorted(categories)) or '（全部为其他）'}",
        )
    finally:
        await crawler.stop()


async def main() -> int:
    url = os.environ.get("SMOKE_CRAWL_URL", "").strip()
    rpc_url = os.environ.get("SMOKE_ARIA2_RPC_URL", "").strip()
    secret = os.environ.get("SMOKE_ARIA2_SECRET", "")
    save_path = os.environ.get("SMOKE_ARIA2_SAVE_PATH", "").strip()
    state_db = Path(os.environ.get("SMOKE_STATE_DB", "data/smoke-aria2.db"))
    cookies_raw = os.environ.get("SMOKE_SITE_COOKIES", "").strip()
    submit = os.environ.get("SMOKE_SUBMIT", "").strip() == "1"

    if not url:
        print("缺少 SMOKE_CRAWL_URL（要抓取的真实站点 URL）")
        return 1
    if not (rpc_url and secret):
        print("缺少 SMOKE_ARIA2_RPC_URL / SMOKE_ARIA2_SECRET")
        return 1
    if submit:
        print("⚠ SMOKE_SUBMIT=1：将向下载中心提交占位 magnet 并轮询；任务保留，请验证后手动清理")

    print("=== 生产链路 smoke 验证 ===")
    print(f"目标站点: {url}")
    print(f"下载中心提交模式: {'开启' if submit else '关闭'}\n")

    await _check_bridge(rpc_url, secret, save_path, state_db, submit)
    try:
        await _check_crawl(url, cookies_raw)
    except Exception as exc:  # noqa: BLE001
        _record("站点抓取", False, f"{type(exc).__name__}: {exc}")
        if os.environ.get("SMOKE_DEBUG"):
            traceback.print_exc()

    print("\n=== 汇总 ===")
    failed = [name for name, ok, _ in _STEPS if not ok]
    for name, ok, detail in _STEPS:
        print(f"  {'✔' if ok else '✘'} {name}")
    if failed:
        print(f"\nFAIL: {len(failed)} 个步骤未通过: {', '.join(failed)}")
        return 1
    print(
        "\n全部通过。注意：这验证的是真实环境链路可用，"
        "不等同于 NAS 下载目录与生产流量已做完整验收。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
