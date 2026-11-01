"""取页模块的独立量测入口。

    # 人类可读：逐条打印 + 最后一张表
    python -m magnet_harvester.crawl_cli "https://site/list/..."

    # 机器可读：只打印最后一行 JSON（给脚本/CI 对比用）
    python -m magnet_harvester.crawl_cli "https://..." --json

    # 同一进程连跑 N 次，看「第二次」的复用效果（HTTP 会话/浏览器复用）
    python -m magnet_harvester.crawl_cli "https://..." --runs 2

这个入口不依赖中心服务、不写数据库、不提交 qB —— 只量取页这一段。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from magnet_harvester.config import CrawlerConfig, settings
from magnet_harvester.crawler import MagnetCrawler


def _config_from_args(args: argparse.Namespace) -> CrawlerConfig:
    config = settings.crawler
    if args.depth:
        config.max_depth = max(config.max_depth, int(args.depth))
    if args.no_browser:
        config.browser_fallback = False
    if args.concurrency:
        config.per_host_concurrency = max(1, int(args.concurrency))
    if args.min_interval is not None:
        config.per_host_min_interval = max(0.0, float(args.min_interval))
    return config


async def _run_once(
    crawler: MagnetCrawler,
    url: str,
    depth: int,
    *,
    verbose: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    first_item_at: float | None = None
    items: list[dict] = []
    errors: list[dict] = []
    events = 0
    metrics: dict[str, Any] = {}
    async for event in crawler.crawl(url, depth=depth):
        events += 1
        kind = event.get("type")
        if kind == "found":
            if first_item_at is None:
                first_item_at = time.monotonic() - started
            items.append(event["item"])
            if verbose:
                item = event["item"]
                mark = (
                    f"{first_item_at * 1000:.0f}ms"
                    if len(items) == 1
                    else f"{(time.monotonic() - started) * 1000:.0f}ms"
                )
                print(f"  + [{mark}] {item['hash'][:12]}… {item.get('name', '')[:70]}")
        elif kind == "error":
            errors.append(event)
            if verbose:
                print(
                    f"  ! [{(time.monotonic() - started) * 1000:.0f}ms] "
                    f"{event.get('reason')}: {event.get('msg')} {event.get('url', '')}"
                )
        elif kind == "done":
            metrics = event.get("metrics", {})
    total_ms = int((time.monotonic() - started) * 1000)
    return {
        "url": url,
        "depth": depth,
        "first_item_ms": int(first_item_at * 1000) if first_item_at is not None else None,
        "total_ms": total_ms,
        "items": len(items),
        "pages": metrics.get("pages_crawled", 0),
        "browser_used": metrics.get("browser_used", False),
        "errors_by_reason": metrics.get("errors_by_reason", {}),
        "events": events,
        "page_detail": metrics.get("pages", []),
        "error_messages": [
            {"reason": e.get("reason"), "msg": e.get("msg"), "url": e.get("url")} for e in errors
        ],
    }


async def _main_async(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    crawler = MagnetCrawler(config=config)
    reports: list[dict[str, Any]] = []
    try:
        await crawler.start()
        for run in range(max(1, args.runs)):
            if args.verbose:
                print(f"== run {run + 1}/{args.runs} ==")
            reports.append(await _run_once(crawler, args.url, args.depth, verbose=args.verbose))
    finally:
        await crawler.stop()

    summary = reports[-1] if len(reports) == 1 else {"runs": reports}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    for index, report in enumerate(reports, start=1):
        print()
        print(f"—— run {index} ——")
        print(f"  URL            {report['url']}")
        print(f"  首条           {_fmt_ms(report['first_item_ms'])}")
        print(f"  整轮           {_fmt_ms(report['total_ms'])}")
        print(f"  条目           {report['items']}")
        print(f"  取页           {report['pages']} 页")
        print(f"  浏览器         {'用了' if report['browser_used'] else '没用（纯 HTTP）'}")
        if report["errors_by_reason"]:
            print(f"  失败原因       {report['errors_by_reason']}")
        for page in report["page_detail"]:
            status = page.get("reason") or f"{page.get('magnets')} 条"
            print(
                f"    - {page.get('via'):7} {page.get('status'):>3} "
                f"{page.get('ms'):>5}ms {status:>10}  {page.get('url')}"
            )
    return 0


def _fmt_ms(value: int | None) -> str:
    if value is None:
        return "—（没找到磁力）"
    return f"{value} ms ({value / 1000:.2f}s)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magnet_harvester.crawl_cli",
        description="量一次取页：首条延迟、整轮耗时、页数、是否用浏览器、失败原因。",
    )
    parser.add_argument("url", help="要抓的页面地址（用户复制的那个 URL）")
    parser.add_argument("--depth", type=int, default=1, help="跟随层级（1=根页+直接详情页）")
    parser.add_argument("--runs", type=int, default=1, help="同一进程内连跑几次（测会话复用）")
    parser.add_argument("--concurrency", type=int, default=0, help="每站点并发上限")
    parser.add_argument("--min-interval", type=float, default=None, help="每站点请求最小间隔（秒）")
    parser.add_argument("--no-browser", action="store_true", help="禁用浏览器升级（只量 HTTP）")
    parser.add_argument("--json", action="store_true", help="最后打印一行 JSON 摘要")
    parser.add_argument("--quiet", dest="verbose", action="store_false", help="不打印逐条事件")
    parser.set_defaults(verbose=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.json:
        args.verbose = False  # --json 只吐最后一行，别把逐条事件混进来
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
