"""N23 页面侧验收：干净浏览器打开即读写、浏览器零密钥、只读说人话、旧键只清不读。

这些用例在旧实现上会红——旧页面靠 `#key=` 片段把钥匙塞进 localStorage，
没钥匙就把鉴权条/WS/qB/采集画成红灰，保存并测试直接报「请求缺少 API Key」。
现在页面状态只由 `/api/auth` 的 `write` 决定：trusted 直接可写，readonly
只读且人话，真实故障（500/网络断）仍然红。
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright

WS_STUB = """
window.__ws = { urls: [], sent: [], closed: [], last: null };
window.WebSocket = class {
  static OPEN = 1;
  constructor(url) {
    window.__ws.urls.push(url);
    window.__ws.last = this;
    this.readyState = 0;
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
    }, 0);
  }
  send(data) { window.__ws.sent.push(data); }
  close(code = 1000, reason = "") {
    window.__ws.closed.push([code, reason]);
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
};
"""

LEGACY_KEY = "".join(["legacy", "-ui-", "key"])


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


def _iso(*, minutes: int) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return moment.isoformat(timespec="seconds")


def _items(count: int = 58) -> dict:
    rows = [
        {
            "hash": f"HASH{i:036d}",
            "name": f"Paused {i}",
            "status": "paused",
            "torrent_state": "stoppedDL",
            "category": "电影",
            "source_kind": "mac_magnet",
            "created_at": _iso(minutes=120),
        }
        for i in range(count - 1)
    ]
    rows.append(
        {
            "hash": "DONE" + "0" * 36,
            "name": "Finished",
            "status": "success",
            "category": "电影",
            "source_kind": "mac_magnet",
            "created_at": _iso(minutes=110),
        }
    )
    return {"items": rows, "total": count}


class UiApiMock:
    """复刻 N23 服务端：/api/auth 回答 write；写接口在 trusted 下不带 key 也收。"""

    def __init__(self, *, mode: str = "trusted", status_override: dict | None = None):
        self.requests: list[dict] = []
        self.mode = mode
        self.status_override = status_override or {}

    async def handle(self, route, request):
        path = urlparse(request.url).path
        method = request.method
        self.requests.append(
            {
                "path": path,
                "method": method,
                "key": request.headers.get("x-api-key", ""),
                "origin": request.headers.get("origin", ""),
                "body": request.post_data,
            }
        )
        for suffix, (status, payload) in self.status_override.items():
            if path.endswith(suffix):
                await route.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(payload, ensure_ascii=False),
                )
                return

        if path.endswith("/api/auth"):
            payload = {
                "mode": self.mode,
                "trusted": self.mode == "trusted",
                "write": self.mode == "trusted",
                "required": self.mode == "strict",
                "key": "not_required" if self.mode == "trusted" else "missing",
                "reason": "trusted_lan" if self.mode == "trusted" else "strict_mode",
            }
        elif path.endswith("/api/config") and method == "GET":
            payload = {
                "qbit_host": "http://192.168.20.193:8085",
                "qbit_username": "qb-user",
                "qbit_password_configured": True,
            }
        elif path.endswith("/api/config") and method == "PUT":
            payload = {"status": "ok", "connected": True}
        elif path.endswith("/api/v1/status"):
            payload = {"qbit": {"ok": True}, "jobs": [], "recent_events": []}
        elif path.endswith("/api/items") and method == "GET":
            payload = _items()
        elif path.endswith("/api/items") and method == "DELETE":
            payload = {"status": "cleared", "removed": 58}
        elif path.endswith("/api/status"):
            payload = {"qbittorrent": "online"}
        else:
            payload = {}
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )


async def _open(playwright, url, *, api: UiApiMock | None = None, seed_legacy: bool = False):
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()  # 干净上下文：没有任何 storage/cookie
    page = await context.new_page()
    await page.add_init_script(WS_STUB)
    if seed_legacy:
        # 模拟上一版页面留下的钥匙：清理脚本必须在 app.js 之前把它删掉
        await page.add_init_script(
            "try { localStorage.setItem('magnet-api-key', %s);"
            " sessionStorage.setItem('magnet-api-key', %s); } catch (e) {}"
            % (json.dumps(LEGACY_KEY), json.dumps(LEGACY_KEY))
        )
    mock = api or UiApiMock()
    await page.route("**/api/**", mock.handle)
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(400)
    return browser, context, page, mock


@pytest.mark.asyncio
async def test_clean_context_opens_trusted_without_auth_banner(static_server_url):
    """用户从菜单栏打开正式档的第一屏：局域网内直接可写，没有鉴权横幅。"""
    async with async_playwright() as playwright:
        browser, _, page, _ = await _open(playwright, static_server_url)
        auth_text = await page.locator("#authState").inner_text()
        auth_class = await page.locator("#authState").get_attribute("class")
        hint_hidden = await page.locator("#authHint").evaluate("(el) => el.hidden")
        body_mode = await page.locator("body").get_attribute("data-auth-mode")
        save_enabled = await page.locator("#saveConfigBtn").is_enabled()
        toast_count = await page.locator("#toastStack .toast").count()
        await browser.close()

    assert "局域网内可直接读写" in auth_text
    assert "ok" in (auth_class or "")
    assert hint_hidden is True, "trusted 下不该有横幅/提示条"
    assert body_mode == "trusted"
    assert save_enabled is True
    assert toast_count == 0, "打开页面不该因为「授权」弹任何提示"


@pytest.mark.asyncio
async def test_trusted_page_writes_without_key_and_stores_nothing(static_server_url):
    """写操作不带 key；页面不写任何浏览器存储，地址栏没有凭据。"""
    async with async_playwright() as playwright:
        browser, context, page, mock = await _open(playwright, static_server_url)
        await page.locator("#cfgHost").fill("http://192.168.20.193:8085")
        await page.locator("#cfgUser").fill("qb-user")
        await page.locator("#saveConfigBtn").click()
        await page.wait_for_timeout(300)

        storage = await page.evaluate(
            """
            () => ({
              local: Object.keys(localStorage),
              session: Object.keys(sessionStorage),
              hash: location.hash,
              sent: window.__ws.sent.slice(),
            })
            """
        )
        cookies = await context.cookies()
        await browser.close()

    puts = [r for r in mock.requests if r["method"] == "PUT"]
    assert len(puts) == 1
    assert puts[0]["key"] == "", "浏览器不该带 X-API-Key"
    assert storage["local"] == [], "localStorage 里不该有 key"
    assert storage["session"] == [], "sessionStorage 里不该有 key"
    assert storage["hash"] == ""
    assert cookies == []
    frames = [json.loads(item) for item in storage["sent"] if item.startswith("{")]
    assert all("api_key" not in frame for frame in frames)


@pytest.mark.asyncio
async def test_legacy_key_is_cleared_and_never_used(static_server_url):
    """旧版本留在浏览器里的 magnet-api-key：只清不读，也不拿它去写。"""
    async with async_playwright() as playwright:
        browser, _, page, mock = await _open(
            playwright, static_server_url, seed_legacy=True
        )
        state = await page.evaluate(
            """
            () => ({
              local: localStorage.getItem('magnet-api-key'),
              session: sessionStorage.getItem('magnet-api-key'),
            })
            """
        )
        auth_text = await page.locator("#authState").inner_text()
        await browser.close()

    assert state["local"] is None
    assert state["session"] is None
    assert "局域网内可直接读写" in auth_text
    assert all(r["key"] == "" for r in mock.requests), "旧 key 不许被拿来发请求"


@pytest.mark.asyncio
async def test_legacy_fragment_is_scrubbed_without_being_consumed(static_server_url):
    """#key= 片段交接已作废：地址栏清干净，但不解析、不落盘、不带去请求。"""
    async with async_playwright() as playwright:
        browser, _, page, mock = await _open(
            playwright, f"{static_server_url}#key={LEGACY_KEY}"
        )
        state = await page.evaluate(
            """
            () => ({
              hash: location.hash,
              local: localStorage.getItem('magnet-api-key'),
              session: sessionStorage.getItem('magnet-api-key'),
              sent: window.__ws.sent.slice(),
            })
            """
        )
        await browser.close()

    assert state["hash"] == ""
    assert state["local"] is None
    assert state["session"] is None
    assert all(r["key"] == "" for r in mock.requests)
    frames = [json.loads(item) for item in state["sent"] if item.startswith("{")]
    assert all("api_key" not in frame for frame in frames)


@pytest.mark.asyncio
async def test_readonly_mode_is_honest_and_never_sends_writes(static_server_url):
    """严格模式：只读、人话提示、不出现「请求缺少 API Key」，写操作根本不发。"""
    api = UiApiMock(mode="strict")
    async with async_playwright() as playwright:
        browser, _, page, mock = await _open(
            playwright, static_server_url, api=api
        )
        save_disabled = await page.locator("#saveConfigBtn").is_disabled()
        await page.evaluate("() => saveConfig()")  # 绕过 disabled 直接调用也要挡住
        await page.wait_for_timeout(200)
        cfg_text = await page.locator("#cfgResult").inner_text()
        page_text = await page.locator("body").inner_text()
        toast_text = await page.locator("#toastStack").inner_text()
        await browser.close()

    writes = [r for r in mock.requests if r["method"] in {"PUT", "POST", "DELETE"}]
    assert writes == [], "只读设备不该发任何写请求"
    assert save_disabled is True
    assert "只读" in cfg_text
    assert "请求缺少 API Key" not in page_text
    assert "只读模式" in toast_text


@pytest.mark.asyncio
async def test_real_status_failure_is_still_red(static_server_url):
    """真实故障照旧红：状态接口 500 不能被说成「没授权」。"""
    api = UiApiMock(
        status_override={"/api/v1/status": (500, {"detail": "boom", "code": "internal_error"})}
    )
    async with async_playwright() as playwright:
        browser, _, page, _ = await _open(playwright, static_server_url, api=api)
        qbit_text = await page.locator("#qbitStatus").inner_text()
        qbit_dot = await page.locator("#dotQbit").get_attribute("class")
        auth_text = await page.locator("#authState").inner_text()
        await browser.close()

    assert "状态接口失败（500）" in qbit_text
    assert "offline" in (qbit_dot or "")
    assert "局域网内可直接读写" in auth_text


@pytest.mark.asyncio
async def test_trusted_page_connects_realtime_without_key(static_server_url):
    """实时通道：trusted 下页面主动连 WS，首帧只声明订阅、不带凭据。"""
    async with async_playwright() as playwright:
        browser, _, page, _ = await _open(playwright, static_server_url)
        sent = await page.evaluate("() => window.__ws.sent.slice()")
        urls = await page.evaluate("() => window.__ws.urls.slice()")
        await page.evaluate(
            "() => handleMsg({type: 'hello', mode: 'trusted', auth_required: false})"
        )
        ws_text = await page.locator("#wsStatus").inner_text()
        ws_dot = await page.locator("#dotWs").get_attribute("class")
        await browser.close()

    assert len(urls) == 1 and "api_key" not in urls[0]
    frames = [json.loads(item) for item in sent if item.startswith("{")]
    assert frames and frames[0].get("type") == "auth"
    assert "api_key" not in frames[0]
    assert "已连接" in ws_text
    assert "online" in (ws_dot or "")
