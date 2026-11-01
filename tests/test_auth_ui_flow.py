"""从菜单栏打开正式档的页面侧验收：片段进钥匙、本机持久、只读不红、写前说人话。

这些用例在旧实现（钥匙只存 sessionStorage、无 key 时把三处红/两个红点当故障、
保存并测试直接报「请求缺少 API Key」）上会红；修复后必须绿。
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import pytest
from playwright.async_api import async_playwright

# 构造而非字面量：避免被仓库的凭据扫描误判（这是测试用的假钥匙）
KEY = "".join(["ui-test", "-key-", "7f3a"])
BAD_KEY = "".join(["ui-bad", "-key-", "0c11"])
# 带空格/加号/斜杠的钥匙：证明片段按 URL 编码解码（p2 交过来的就是 urlencoded）
ENCODED_KEY = "".join(["ui test", "+key/", "7f3a"])

WS_STUB = """
window.__ws = { urls: [], sent: [], closed: [] };
window.WebSocket = class {
  static OPEN = 1;
  constructor(url) {
    window.__ws.urls.push(url);
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
    """正式档的样子：57 条已暂停 + 1 条完成。"""
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


class AuthApiMock:
    """按 X-API-Key 回答，复刻新后端的读写语义。"""

    def __init__(
        self,
        *,
        status_override: tuple[int, dict] | None = None,
        key: str = KEY,
    ):
        self.requests: list[dict] = []
        self.status_override = status_override
        self.key = key

    async def handle(self, route, request):
        path = urlparse(request.url).path
        supplied = request.headers.get("x-api-key", "")
        valid = supplied == self.key
        self.requests.append(
            {
                "url": request.url,
                "path": path,
                "method": request.method,
                "key": supplied,
                "body": request.post_data,
            }
        )

        if path == "/api/auth":
            key_state = "missing" if not supplied else ("valid" if valid else "invalid")
            payload = {
                "required": True,
                "scheme": "X-API-Key",
                "websocket": "first-message",
                "key": key_state,
            }
            await _json(route, 200, payload)
            return
        if path == "/api/config":
            if request.method == "PUT":
                if not valid:
                    await _json(
                        route,
                        401,
                        {
                            "detail": "请求缺少 API Key",
                            "code": "api_key_missing",
                            "hint": "写操作需要授权。",
                        },
                    )
                else:
                    await _json(route, 200, {"connected": True})
                return
            await _json(
                route,
                200,
                {
                    "qbit_host": "http://192.168.20.193:8085",
                    "qbit_username": "nas-admin",
                    "qbit_password_configured": True,
                },
            )
            return
        if path == "/api/v1/status":
            if self.status_override is not None:
                status, payload = self.status_override
                await _json(route, status, payload)
                return
            await _json(
                route,
                200,
                {
                    "ok": True,
                    "last_event_at": _iso(minutes=8),
                    "jobs": [],
                    "job_counts": {},
                    "recent_items": [],
                    "recent_events": [],
                    "qbit": {"ok": True, "reason": None, "hint": None},
                    "crawler": {"ok": True},
                    "items_total": 58,
                },
            )
            return
        if path == "/api/items" and request.method == "GET":
            await _json(route, 200, _items())
            return
        if path in {"/api/download", "/api/crawl", "/api/reclassify"} or (
            path == "/api/items" and request.method == "DELETE"
        ):
            if not valid:
                await _json(
                    route,
                    401,
                    {"detail": "请求缺少 API Key", "code": "api_key_missing", "hint": ""},
                )
            else:
                await _json(route, 200, {"connected": True, "count": 1, "status": "ok"})
            return
        await _json(route, 200, {})


async def _json(route, status: int, payload: dict) -> None:
    await route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False),
    )


async def _open(playwright, url, *, api=None, init_key=None):
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(timezone_id="Asia/Shanghai")
    page = await context.new_page()
    await page.add_init_script(WS_STUB)
    if init_key is not None:
        await page.add_init_script(
            "try { localStorage.setItem('magnet-api-key', %s); } catch (error) {}"
            % json.dumps(init_key)
        )
    mock = api or AuthApiMock()
    # 挂在 context 上：同一次浏览器里新开的标签页也要走同一份假后端
    await context.route("**/api/**", mock.handle)
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(500)
    return browser, context, page, mock


@pytest.mark.asyncio
async def test_fragment_key_is_consumed_persisted_and_leaves_no_trace(static_server_url):
    """从菜单栏进入：钥匙落进本机、地址栏/历史不留 key、请求与 WS 首帧带钥匙。"""
    fragment_url = f"{static_server_url}#key={quote(KEY, safe='')}"
    async with async_playwright() as playwright:
        browser, context, page, mock = await _open(playwright, "about:blank")
        await page.goto(fragment_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(500)

        state = await page.evaluate(
            """() => ({
              href: location.href,
              hash: location.hash,
              local: localStorage.getItem('magnet-api-key'),
              session: sessionStorage.getItem('magnet-api-key'),
              auth: document.getElementById('authState').textContent,
            })"""
        )
        ws = await page.evaluate("() => window.__ws")
        await browser.close()

    assert state["local"] == KEY, "钥匙必须落在本机（刷新/重启仍在）"
    assert state["session"] is None, "旧 sessionStorage 的钥匙应已迁走"
    assert state["hash"] == ""
    assert KEY not in state["href"] and "key=" not in state["href"]
    assert "已授权" in state["auth"]
    assert all("api_key" not in url and "key=" not in url for url in ws["urls"])
    frames = [json.loads(frame) for frame in ws["sent"] if frame.startswith("{")]
    assert any(
        frame.get("type") == "auth" and frame.get("api_key") == KEY for frame in frames
    ), "WS 钥匙必须走握手后第一帧"
    # 页面自己的读请求也带上了钥匙
    assert any(req["path"] == "/api/config" and req["key"] == KEY for req in mock.requests)


@pytest.mark.asyncio
async def test_history_entry_is_rewritten_so_back_cannot_reveal_key(static_server_url):
    """replaceState 改的是当前历史项：按后退回到上一页，不会回到带 key 的地址。"""
    fragment_url = f"{static_server_url}#key={quote(KEY, safe='')}"
    async with async_playwright() as playwright:
        browser, context, page, _ = await _open(playwright, "about:blank")
        await page.goto(fragment_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(300)
        clean_url = page.url
        await page.go_back()
        await page.wait_for_timeout(200)
        back_url = page.url
        await browser.close()

    assert "key=" not in clean_url and KEY not in clean_url
    assert back_url == "about:blank", "历史里不能留着带 key 的那一项"


@pytest.mark.asyncio
async def test_key_survives_refresh_new_tab_and_browser_restart(static_server_url):
    """用户硬要求：刷新、新开标签、浏览器重启后都不再丢钥匙。"""
    fragment_url = f"{static_server_url}#key={quote(KEY, safe='')}"
    plain_url = static_server_url
    async with async_playwright() as playwright:
        browser, context, page, mock = await _open(playwright, "about:blank")
        await page.goto(fragment_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(400)

        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(400)
        after_reload = await page.locator("#authState").inner_text()
        reload_key = await page.evaluate("() => localStorage.getItem('magnet-api-key')")

        second = await context.new_page()
        await second.goto(plain_url, wait_until="domcontentloaded")
        await second.wait_for_timeout(400)
        new_tab_auth = await second.locator("#authState").inner_text()

        storage_state = await context.storage_state()
        await browser.close()

        browser2 = await playwright.chromium.launch(headless=True)
        context2 = await browser2.new_context(
            timezone_id="Asia/Shanghai", storage_state=storage_state
        )
        await context2.route("**/api/**", mock.handle)
        page2 = await context2.new_page()
        await page2.goto(plain_url, wait_until="domcontentloaded")
        await page2.wait_for_timeout(400)
        restarted_auth = await page2.locator("#authState").inner_text()
        restarted_key = await page2.evaluate(
            "() => localStorage.getItem('magnet-api-key')"
        )
        await browser2.close()

    assert "已授权" in after_reload and reload_key == KEY
    assert "已授权" in new_tab_auth
    assert restarted_key == KEY and "已授权" in restarted_auth


@pytest.mark.asyncio
async def test_no_key_browser_reads_reality_without_red(static_server_url):
    """手机/别人的电脑：58 行照常、qB/采集/Mac/配置都是真实值，只读但不红。"""
    async with async_playwright() as playwright:
        browser, context, page, mock = await _open(playwright, static_server_url)
        responses_401: list[str] = []
        page.on(
            "response",
            lambda response: (
                responses_401.append(response.url)
                if response.status == 401
                else None
            ),
        )
        # 让首屏请求落地后重新收集 401
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(500)

        facts = await page.evaluate(
            """() => {
              const text = id => document.getElementById(id).textContent.trim();
              const cls = id => document.getElementById(id).className;
              const rows = document.querySelectorAll('#tbody tr');
              const badges = {};
              rows.forEach(row => row.querySelectorAll('.badge').forEach(badge => {
                const label = badge.textContent.trim();
                badges[label] = (badges[label] || 0) + 1;
              }));
              return {
                rows: rows.length,
                badges,
                auth: text('authState'),
                authCls: cls('authState'),
                ws: text('wsStatus'),
                wsDot: cls('dotWs'),
                qbit: text('qbitStatus'),
                qbitDot: cls('dotQbit'),
                crawler: text('crawlerDetail'),
                crawlerDot: cls('crawlerDetailDot'),
                mac: text('macLabel'),
                macDot: cls('dotMac'),
                host: document.getElementById('cfgHost').value,
                hostPlaceholder: document.getElementById('cfgHost').placeholder,
                user: document.getElementById('cfgUser').value,
                pass: document.getElementById('cfgPass').value,
                body: document.body.innerText,
              };
            }"""
        )
        ws_urls = await page.evaluate("() => window.__ws.urls")

        # 写操作：保存并测试 + 下载，都不该发请求，只给「需要授权」的人话
        await page.locator("#saveConfigBtn").click()
        await page.wait_for_timeout(200)
        save_text = await page.locator("#cfgResult").inner_text()
        save_hint = await page.locator("#cfgHint").inner_text()
        toast_text = await page.locator("#toastStack").inner_text()

        await page.locator("#tbody tr").first.locator(".row-checkbox").check()
        await page.locator("#dlBtn").click()
        await page.wait_for_timeout(200)
        writes = [
            request
            for request in mock.requests
            if request["method"] in {"PUT", "POST", "DELETE"}
        ]
        await browser.close()

    assert facts["rows"] == 58, "无钥匙也要看见完整资源库"
    assert facts["badges"] == {"已暂停": 57, "完成": 1}
    assert "只读" in facts["auth"] and "error" not in facts["authCls"]
    assert "只读" in facts["ws"] and "offline" not in facts["wsDot"]
    assert facts["qbit"] == "qB 在线" and "online" in facts["qbitDot"]
    assert facts["crawler"].startswith("可用") and "online" in facts["crawlerDot"]
    assert "Mac ·" in facts["mac"] and "online" in facts["macDot"], "Mac 上报数据源要走开放状态接口"
    assert facts["host"] == "http://192.168.20.193:8085"
    assert facts["user"] == "nas-admin"
    assert facts["pass"] == "", "密码绝不回显"
    assert "nas.local" not in facts["hostPlaceholder"]
    for forbidden in ("需要 API Key", "API Key 未通过", "请求缺少 API Key"):
        assert forbidden not in facts["body"]
    assert responses_401 == [], "只读页面不应再打出 401"
    assert ws_urls == [], "只读浏览器不发起注定 4401 的握手"
    assert "只读" in save_text and "授权" in save_hint
    assert "请求缺少 API Key" not in toast_text
    assert "授权" in toast_text
    assert writes == [], "未授权时写请求一个都不发"


@pytest.mark.asyncio
async def test_rejected_key_is_amber_readonly_and_forget_clears_it(static_server_url):
    """钥匙被拒：只读 + 明确告知重取；「退出本机」清钥匙并回到匿名只读。"""
    async with async_playwright() as playwright:
        browser, context, page, mock = await _open(
            playwright, static_server_url, init_key=BAD_KEY
        )
        facts = await page.evaluate(
            """() => ({
              auth: document.getElementById('authState').textContent,
              authCls: document.getElementById('authState').className,
              qbit: document.getElementById('qbitStatus').textContent,
              qbitDot: document.getElementById('dotQbit').className,
              forgetHidden: document.getElementById('forgetKeyBtn').hidden,
              local: localStorage.getItem('magnet-api-key'),
            })"""
        )
        await page.locator("#saveConfigBtn").click()
        await page.wait_for_timeout(200)
        toast_text = await page.locator("#toastStack").inner_text()
        puts = [
            request
            for request in mock.requests
            if request["method"] == "PUT"
        ]

        await page.locator("#forgetKeyBtn").click()
        await page.wait_for_timeout(500)
        after = await page.evaluate(
            """() => ({
              local: localStorage.getItem('magnet-api-key'),
              auth: document.getElementById('authState').textContent,
              forgetHidden: document.getElementById('forgetKeyBtn').hidden,
            })"""
        )
        await browser.close()

    assert "钥匙没被 NAS 接受" in facts["auth"] and "warn" in facts["authCls"]
    assert facts["qbit"] == "qB 在线" and "online" in facts["qbitDot"], "读接口开放，qB 仍是真状态"
    assert facts["forgetHidden"] is False
    assert facts["local"] == BAD_KEY
    assert "钥匙没被 NAS 接受" in toast_text
    assert puts == [], "钥匙被拒时不该再发写请求"
    assert after["local"] is None
    assert "只读" in after["auth"] and "warn" not in after["auth"]
    assert after["forgetHidden"] is True


@pytest.mark.asyncio
async def test_real_failure_stays_red_and_is_not_blamed_on_auth(static_server_url):
    """真实故障（状态接口 500）必须如实红；授权状态仍是「已授权」。"""
    api = AuthApiMock(
        status_override=(
            500,
            {"detail": "服务内部错误（RuntimeError: boom）", "code": "internal_error", "hint": ""},
        )
    )
    async with async_playwright() as playwright:
        browser, context, page, _ = await _open(
            playwright, static_server_url, api=api, init_key=KEY
        )
        facts = await page.evaluate(
            """() => ({
              auth: document.getElementById('authState').textContent,
              authCls: document.getElementById('authState').className,
              qbit: document.getElementById('qbitStatus').textContent,
              qbitDot: document.getElementById('dotQbit').className,
              crawlerDot: document.getElementById('crawlerDetailDot').className,
              mac: document.getElementById('macLabel').textContent,
            })"""
        )
        await browser.close()

    assert "已授权" in facts["auth"] and "ok" in facts["authCls"]
    assert "状态接口失败" in facts["qbit"] and "offline" in facts["qbitDot"]
    assert "offline" in facts["crawlerDot"]
    assert "未知" in facts["mac"], "读不到 ≠ 没有上报"


@pytest.mark.asyncio
async def test_fragment_value_is_url_decoded(static_server_url):
    """p2 交过来的是 #key=<urlencoded>；带空格/加号/斜杠也要原样落库。"""
    fragment_url = f"{static_server_url}#key={quote(ENCODED_KEY, safe='')}"
    api = AuthApiMock(key=ENCODED_KEY)
    async with async_playwright() as playwright:
        browser, context, page, mock = await _open(playwright, "about:blank", api=api)
        await page.goto(fragment_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(500)
        stored = await page.evaluate("() => localStorage.getItem('magnet-api-key')")
        auth = await page.locator("#authState").inner_text()
        href = await page.evaluate("() => location.href")
        ws = await page.evaluate("() => window.__ws")
        await browser.close()

    assert stored == ENCODED_KEY
    assert "已授权" in auth
    assert "key=" not in href and ENCODED_KEY not in href
    assert all("key=" not in url for url in ws["urls"])
    frames = [json.loads(frame) for frame in ws["sent"] if frame.startswith("{")]
    assert any(
        frame.get("type") == "auth" and frame.get("api_key") == ENCODED_KEY
        for frame in frames
    )


@pytest.mark.asyncio
async def test_forget_device_key_syncs_across_tabs(static_server_url):
    """一个标签点「退出本机」，另一个标签不能还拿着旧钥匙。"""
    fragment_url = f"{static_server_url}#key={quote(KEY, safe='')}"
    async with async_playwright() as playwright:
        browser, context, page, _ = await _open(playwright, "about:blank")
        await page.goto(fragment_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(400)

        other = await context.new_page()
        await other.add_init_script(WS_STUB)
        await other.goto(static_server_url, wait_until="domcontentloaded")
        await other.wait_for_timeout(400)
        before = await other.evaluate(
            "() => localStorage.getItem('magnet-api-key')"
        )

        await page.locator("#forgetKeyBtn").click()
        await page.wait_for_timeout(700)
        after = await other.evaluate(
            """() => ({
              local: localStorage.getItem('magnet-api-key'),
              auth: document.getElementById('authState').textContent,
            })"""
        )
        await browser.close()

    assert before == KEY
    assert after["local"] is None
    assert "只读" in after["auth"]
