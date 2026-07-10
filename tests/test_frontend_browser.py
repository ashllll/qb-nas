"""Real-browser smoke tests for the static Web UI module seams."""

from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest
from playwright.async_api import async_playwright


@pytest.fixture(scope="module")
def static_server_url():
    root = Path(__file__).parent.parent
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/static/index.html"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.asyncio
async def test_frontend_loads_css_and_javascript_as_local_modules(static_server_url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        console_problems: list[str] = []
        page.on(
            "console",
            lambda message: console_problems.append(message.text)
            if message.type in {"error", "warning"}
            else None,
        )
        await page.add_init_script(
            """
            window.WebSocket = class {
              constructor() { setTimeout(() => this.onopen?.(), 0); }
              send() {}
              close() {}
            };
            """
        )
        await page.route(
            "**/api/**",
            lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
        )
        await page.goto(static_server_url, wait_until="domcontentloaded")

        resources = await page.evaluate(
            "() => performance.getEntriesByType('resource').map((entry) => entry.name)"
        )
        assert any(resource.endswith("/static/styles.css") for resource in resources)
        assert any(resource.endswith("/static/api_client.js") for resource in resources)
        assert any(resource.endswith("/static/app.js") for resource in resources)
        assert await page.evaluate("() => typeof window.MagnetApiClient") == "function"
        assert await page.evaluate("() => typeof window.handleMsg") == "function"
        categories = await page.locator("[data-cat]").evaluate_all(
            "elements => elements.map((element) => element.dataset.cat)"
        )
        assert categories == [
            "all",
            "电影",
            "电视剧",
            "动漫",
            "音乐",
            "游戏",
            "软件",
            "综艺",
            "纪录片",
            "其他",
        ]
        assert await page.locator(".mobile-nav").count() == 1
        assert await page.evaluate("() => typeof window.initIcons") == "function"
        assert all(resource.startswith("http://127.0.0.1:") for resource in resources)
        assert console_problems == []
        await browser.close()


@pytest.mark.asyncio
async def test_frontend_transport_and_item_state_behave_through_browser(static_server_url):
    requests: list[dict] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        console_problems: list[str] = []
        page.on(
            "console",
            lambda message: console_problems.append(message.text)
            if message.type in {"error", "warning"}
            else None,
        )
        await page.add_init_script(
            """
            window.WebSocket = class {
              constructor() { setTimeout(() => this.onopen?.(), 0); }
              send() {}
              close() {}
            };
            """
        )

        async def handle_api(route, request):
            body = json.loads(request.post_data) if request.post_data else None
            requests.append(
                {
                    "url": request.url,
                    "method": request.method,
                    "headers": request.headers,
                    "body": body,
                }
            )
            if request.url.endswith("/api/error422"):
                await route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps({"detail": [{"msg": "Value error, invalid probe"}]}),
                )
                return
            if request.url.endswith("/api/error-text"):
                await route.fulfill(status=503, content_type="text/plain", body="unavailable")
                return
            if (
                request.url.endswith("/api/config")
                and request.method == "PUT"
                and body["qbit_host"] == "http://unauthorized.test"
            ):
                await route.fulfill(
                    status=401,
                    content_type="application/json",
                    body=json.dumps({"detail": "API key invalid"}),
                )
                return
            if request.url.endswith("/api/config") and request.method == "GET":
                payload = {"qbit_host": "http://qbit.test", "qbit_username": "admin"}
            elif request.url.endswith("/api/config") and request.method == "PUT":
                payload = {"connected": True}
            elif request.url.endswith("/api/status"):
                payload = {"qbittorrent": "online", "items_count": 0, "tracked_downloads": 0}
            elif request.url.endswith("/api/clipboard"):
                payload = {"running": False, "magnet_count": 0}
            else:
                payload = {}
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

        await page.route("**/api/**", handle_api)
        await page.goto(static_server_url, wait_until="domcontentloaded")
        await page.locator("#apiKeyInput").fill("test-api-key")
        await page.evaluate("() => new MagnetApiClient().fetch('/api/probe')")

        probe = next(request for request in requests if request["url"].endswith("/api/probe"))
        assert probe["headers"]["x-api-key"] == "test-api-key"
        assert (
            await page.evaluate(
                "() => new MagnetApiClient().fetch('/api/error422').catch(error => error.message)"
            )
            == "invalid probe"
        )
        assert (
            await page.evaluate(
                "() => new MagnetApiClient().fetch('/api/error-text').catch(error => error.message)"
            )
            == "请求失败 (503)"
        )

        await page.locator("#cfgPass").fill("")
        await page.evaluate("() => saveConfig()")
        update = next(
            request
            for request in requests
            if request["url"].endswith("/api/config") and request["method"] == "PUT"
        )
        assert "qbit_password" not in update["body"]

        await page.locator("#cfgHost").fill("http://unauthorized.test")
        await page.evaluate("() => saveConfig()")
        assert await page.locator("#appWindow").get_attribute("data-mobile-view") == "config"
        console_problems.clear()  # 上面的失败响应会被浏览器按预期记录到控制台。

        await page.evaluate(
            """
            () => {
              handleMsg({type: 'init', items: [
                {hash: 'OLD', name: 'Old', status: 'pending', category: '电影'}
              ]});
              handleMsg({type: 'init', items: [
                {hash: 'NEW', name: 'New', status: 'pending', category: '电影'}
              ]});
            }
            """
        )
        assert await page.locator("#tbody tr").count() == 1
        assert "New" in await page.locator("#tbody").inner_text()
        assert "Old" not in await page.locator("#tbody").inner_text()

        await page.evaluate(
            """
            () => {
              handleMsg({type: 'init', items: [
                {hash: 'MOVIE', name: 'Movie', status: 'pending', category: '电影'},
                {hash: 'MUSIC', name: 'Music', status: 'pending', category: '音乐'}
              ]});
              setFilter('电影');
              selectAllVisible();
              return downloadSelected();
            }
            """
        )
        download = next(
            request for request in requests if request["url"].endswith("/api/download")
        )
        assert download["body"] == {"hashes": ["MOVIE"]}
        assert console_problems == []
        await browser.close()
