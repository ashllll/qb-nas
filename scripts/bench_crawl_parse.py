#!/usr/bin/env python3
"""爬虫同步解析性能基准 — 不入 pytest 门禁。

测量 Magnet Harvester 爬虫同步解析路径在事件循环内的耗时：
1. extract_from_text — 多正则全扫 + Base64 解码（magnet_parser.py）
2. HTML 链接提取近似 — 模拟 scrapling_spider.py 的 css 选择器开销

用法:
    .venv/bin/python scripts/bench_crawl_parse.py

输出: 0.5/1/5MB HTML 三种规模的解析耗时（ms）。

阈值: 若 5MB 规模单次解析 > 100ms，应评估将解析移入 asyncio.to_thread
（见 doc/specs/2026-08-05-fakeip-bench-hardening.md 决策 4）。
"""

from __future__ import annotations

import re
import time

from magnet_harvester.magnet_parser import extract_from_text

_MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"

# 近似 scrapling_spider.py:149 的 css 选择器开销：对全部 <a href> 提取
_LINK_RE = re.compile(rb'<a[^>]*href="([^"]*)"')


def _make_html(size_mb: float, magnet_density: float = 0.1) -> bytes:
    """构造指定大小的 HTML：普通链接为主，穿插 magnet 链接（默认 10% 密度）。"""
    link = f'<a href="https://example.com/page">{_MAGNET}</a>'
    plain = '<a href="https://example.com/page-{i}">普通链接</a><p>内容段落</p>'
    parts: list[str] = []
    size = size_mb * 1024 * 1024
    i = 0
    total = 0
    while total < size:
        if i % 10 < magnet_density * 10:
            parts.append(link)
        else:
            parts.append(plain.format(i=i))
        total += len(parts[-1])
        i += 1
    return "".join(parts).encode()


def _bench_extract(html: bytes) -> float:
    start = time.perf_counter()
    extract_from_text(html.decode("utf-8", errors="ignore"))
    return (time.perf_counter() - start) * 1000


def _bench_css(html: bytes) -> float:
    start = time.perf_counter()
    _LINK_RE.findall(html)
    return (time.perf_counter() - start) * 1000


def main() -> None:
    print(f"{'size':>6} {'len':>9} {'extract(ms)':>12} {'css(ms)':>10}")
    for size in (0.5, 1.0, 5.0):
        html = _make_html(size)
        extract_ms = _bench_extract(html)
        css_ms = _bench_css(html)
        print(f"{size:>5}MB {len(html):>9} {extract_ms:>11.1f} {css_ms:>9.1f}")

    print("\n阈值参考: 5MB 规模 extract > 100ms → 考虑 asyncio.to_thread")


if __name__ == "__main__":
    main()
