#!/usr/bin/env python3
"""生产链路 smoke 验证脚本（可选，不参与常规 pytest 门禁）。

用途：在真实环境验证“测试通过 ≠ 生产链路已验证”的最后一段：
- 真实站点抓取（Scrapling 动态引擎 + 可选 Cookie 注入）
- 真实 qBittorrent 登录与只读 API（分类、torrent 列表）
- 本地分类规则链对真实页面名称的分类结果
- （可选）真实提交一个 magnet 并轮询状态变化

环境变量：
    SMOKE_CRAWL_URL        必填：要抓取的真实站点页面 URL
    SMOKE_QBIT_HOST        必填：qBittorrent Web UI 地址（如 http://192.168.1.100:8080）
    SMOKE_QBIT_USERNAME    必填：qB 用户名
    SMOKE_QBIT_PASSWORD    必填：qB 密码
    SMOKE_SITE_COOKIES     可选：JSON 字符串 {"domain": "cookie-string"}，注入爬虫
    SMOKE_SUBMIT           可选：设为 1 时执行真实提交（add_magnet + 状态轮询），
                                会在 qB 中创建一个 smoke_test 分类并添加一个
                                Debian 官方测试 magnet，下载会真实写入磁盘。

用法：
    SMOKE_CRAWL_URL=... SMOKE_QBIT_HOST=... SMOKE_QBIT_USERNAME=... \
        SMOKE_QBIT_PASSWORD=... python scripts/smoke_production.py

退出码：全部步骤 PASS 为 0，任一 FAIL 为 1。请勿在 NAS 生产下载目录上
直接运行 SMOKE_SUBMIT=1；先确认 qB 默认保存路径可写且愿意接收测试下载。
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.local_classifier import LocalClassificationEngine
from magnet_harvester.config import QBitConfig, CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.services.site_auth import SiteAuth

# 提交链路使用的 magnet：hash 为占位值（格式合法但无真实对等体），
# 仅验证 add_magnet 提交与状态轮询机制；如需真实下载请替换为有效 magnet。
_PLACEHOLDER_MAGNET = (
    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=smoke-test-placeholder"
)

# 步骤结果容器
_STEPS: list[tuple[str, bool, str]] = []


def _record(step: str, ok: bool, detail: str) -> None:
    _STEPS.append((step, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {step}: {detail}")


async def _check_qbit(host: str, user: str, password: str, submit: bool) -> None:
    """真实 qB 登录 + 只读 API + （可选）真实提交轮询。"""
    client = QBittorrentClient(config=QBitConfig(host=host, username=user, password=password))
    try:
        ok = await client.ping()
        if not ok:
            _record("qB 登录", False, f"{host} ping 失败（检查地址/凭据/网络）")
            return
        _record("qB 登录", True, f"{host} 认证通过")

        categories = await client.get_categories()
        _record("qB 分类 API", True, f"读取到 {len(categories)} 个分类")

        snapshot = await client.poll_torrent_snapshot()
        _record("qB torrent 列表", True, f"当前 {len(snapshot)} 个 torrent")

        if not submit:
            _record("qB 提交链路", True, "跳过（SMOKE_SUBMIT=1 时执行真实提交）")
            return

        # 真实提交：创建临时分类 + 添加 magnet + 轮询状态
        await client.ensure_category("smoke_test", save_path="/smoke_test")
        added = await client.add_magnet(
            _PLACEHOLDER_MAGNET,
            category="smoke_test",
            save_path="/smoke_test",
        )
        if not added:
            _record("qB 提交链路", False, "add_magnet 返回失败")
            await _cleanup_smoke(client, hash_key=None)
            return
        _record("qB 提交链路", True, "magnet 已提交，等待状态轮询…")

        seen_states: set[str] = set()
        observed_hash = ""
        for _ in range(10):
            await asyncio.sleep(2)
            snapshot = await client.poll_torrent_snapshot()
            for torrent_hash, torrent in snapshot.items():
                # 占位 hash 被 qB 截断显示，按名称前缀匹配
                name = str(torrent.get("name", ""))
                if "smoke-test-placeholder" in name:
                    observed_hash = torrent_hash
                    seen_states.add(str(torrent.get("state", "unknown")))
            if seen_states:
                break
        if not seen_states:
            _record(
                "qB 提交轮询", False, "10 次轮询内未观察到测试 torrent（add_magnet 可能未生效）"
            )
            await _cleanup_smoke(client, hash_key=None)
            return
        detail = f"观察到状态: {', '.join(sorted(seen_states))}"
        _record("qB 提交轮询", True, detail)
        await _cleanup_smoke(client, hash_key=observed_hash)
    except Exception as exc:  # noqa: BLE001
        _record("qB 链路", False, f"{type(exc).__name__}: {exc}")
        # 异常路径也尽力清理测试产物（无 hash 时至少移除分类）
        await _cleanup_smoke(client, hash_key=None)
    finally:
        await client.close()


async def _cleanup_smoke(client: QBittorrentClient, hash_key: str | None) -> None:
    """清理 smoke 测试产物：删除测试 torrent 与 smoke_test 分类（彼此独立容错）。"""
    # hash_key 为 None（未观察到 torrent）时不声称已删除 torrent
    torrent_cleaned = bool(hash_key)
    if hash_key:
        try:
            await client.request(
                "POST",
                "/torrents/delete",
                params={"hashes": hash_key, "deleteFiles": "false"},
            )
        except Exception as exc:  # noqa: BLE001
            torrent_cleaned = False
            print(f"   [清理] 删除测试 torrent 失败: {exc}")
    category_cleaned = True
    try:
        await client.request(
            "POST",
            "/torrents/removeCategories",
            params={"categories": "smoke_test"},
        )
    except Exception as exc:  # noqa: BLE001
        category_cleaned = False
        print(f"   [清理] 删除 smoke_test 分类失败（可手动删除）: {exc}")
    if torrent_cleaned and category_cleaned:
        print("   [清理] 已删除测试 torrent 与 smoke_test 分类")


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
    host = os.environ.get("SMOKE_QBIT_HOST", "").strip()
    user = os.environ.get("SMOKE_QBIT_USERNAME", "").strip()
    password = os.environ.get("SMOKE_QBIT_PASSWORD", "").strip()
    cookies_raw = os.environ.get("SMOKE_SITE_COOKIES", "").strip()
    submit = os.environ.get("SMOKE_SUBMIT", "").strip() == "1"

    if not url:
        print("缺少 SMOKE_CRAWL_URL（要抓取的真实站点 URL）")
        return 1
    if not (host and user and password):
        print("缺少 SMOKE_QBIT_HOST / SMOKE_QBIT_USERNAME / SMOKE_QBIT_PASSWORD")
        return 1
    if submit:
        print("⚠ SMOKE_SUBMIT=1：将向 qB 提交一个真实测试 magnet 并轮询，请确认保存路径可写")

    print("=== 生产链路 smoke 验证 ===")
    print(f"目标站点: {url}")
    print(f"qB: {host}（提交模式: {'开启' if submit else '关闭'}）\n")

    await _check_qbit(host, user, password, submit)
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
