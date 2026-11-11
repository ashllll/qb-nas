"""丢弃用原型：用 Playwright 在真实 Chromium 里测 5k/50k 的首屏与单事件渲染。

运行：.venv/bin/python prototype/ws_scale/measure.py
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import json
import pathlib
import sys
import threading
from typing import Any

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent
CASES = [(n, mode) for n in (5000, 50000) for mode in ("current", "incremental", "virtual", "page")]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


def start_server() -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(_QuietHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/index.html"


async def run_case(browser: Any, base: str, n: int, mode: str) -> dict[str, Any]:
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    await page.goto(f"{base}?auto=1&n={n}&mode={mode}", wait_until="load", timeout=120_000)
    await page.wait_for_function("window.__result !== undefined", timeout=180_000)
    result = await page.evaluate("window.__result")
    result["errors"] = errors
    await page.close()
    return result


async def main() -> int:
    server, base = start_server()
    results: list[dict[str, Any]] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            for n, mode in CASES:
                print(f"running n={n} mode={mode} ...", flush=True)
                results.append(await run_case(browser, base, n, mode))
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()

    (ROOT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("| N | mode | initial ms | event median ms | event max ms | jump median ms | DOM rows | heap MB |")
    print("|---:|---|---:|---:|---:|---:|---:|---:|")
    for item in results:
        print(
            f"| {item['n']} | {item['mode']} | {item['initialMs']} | {item['eventMedianMs']} | "
            f"{item['eventMaxMs']} | {item['jumpMedianMs']} | {item['domRows']} | {item.get('heapMB')} |"
        )

    bad = [item for item in results if item.get("errors")]
    if bad:
        print("\nPAGE ERRORS:", json.dumps(bad, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
