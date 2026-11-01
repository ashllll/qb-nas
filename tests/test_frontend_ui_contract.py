"""界面契约的真浏览器验收（焦点：失败说真话、暂停不是失败、危险操作不可误触）。

与 `test_frontend_browser.py` 的分工：那边测模块接缝（脚本都能加载、字段能传），
这里测用户真的会看到什么——时间、状态文案/筛选/配色、通知收敛、鉴权提示、
删除作用域。每条都对应一个实测缺陷，修复前会红。
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
from datetime import datetime
from pathlib import Path

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


class ApiMock:
    """按 URL 返回可配置的 JSON；记录请求，供断言路径/参数。"""

    def __init__(self):
        self.requests: list[dict] = []
        self.payloads: dict[str, dict] = {}
        self.status_overrides: dict[str, tuple[int, dict]] = {}

    async def handle(self, route, request):
        body = None
        if request.post_data:
            try:
                body = json.loads(request.post_data)
            except json.JSONDecodeError:
                body = request.post_data
        self.requests.append(
            {"url": request.url, "method": request.method, "body": body}
        )
        path = request.url.split("?")[0]
        for key, response in self.status_overrides.items():
            if path.endswith(key):
                status, payload = response
                await route.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(payload, ensure_ascii=False),
                )
                return
        payload = {
            "config": {"qbit_host": "", "qbit_username": "", "qbit_password_configured": False},
            "status": {"qbit": {"ok": True}, "jobs": [], "recent_events": []},
            "items": {"items": [], "total": 0},
            # N23 默认：局域网内页面可读写（浏览器不持 key）
            "auth": {
                "mode": "trusted",
                "trusted": True,
                "write": True,
                "required": False,
                "key": "not_required",
                "reason": "trusted_lan",
            },
            **self.payloads,
        }.get(path.rsplit("/", 1)[-1], {})
        if request.method == "PUT" and path.endswith("/api/config"):
            payload = self.payloads.get("config_put", {"connected": True})
        if request.method == "DELETE":
            payload = self.payloads.get("delete", {"status": "cleared", "removed": 0})
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )


async def _open(
    playwright,
    static_server_url,
    *,
    api: ApiMock | None = None,
    timezone_id: str = "Asia/Shanghai",
):
    """开一个干净浏览器上下文：N23 下页面不再需要往存储里塞任何 key。"""
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(timezone_id=timezone_id)
    page = await context.new_page()
    await page.add_init_script(WS_STUB)
    mock = api or ApiMock()
    await page.route("**/api/**", mock.handle)
    await page.goto(static_server_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(350)  # 让首屏请求落地，避免与用例里的 handleMsg 抢状态
    return browser, page, mock


@pytest.mark.asyncio
async def test_server_time_is_utc_not_browser_local(static_server_url):
    """缺陷 ①：UTC 容器的裸时间戳被当成本地时间 → 8 分钟前显示成 8 小时前。"""
    async with async_playwright() as playwright:
        browser, page, _ = await _open(
            playwright, static_server_url, timezone_id="Asia/Shanghai"
        )
        result = await page.evaluate(
            """
            () => {
              const naive = "2026-11-01T08:13:36.898188";
              const now = Date.parse("2026-11-01T08:22:00Z");
              return {
                naiveMs: MagnetTime.parse(naive),
                utcMs: Date.parse("2026-11-01T08:13:36.898188Z"),
                relative: MagnetTime.relative(naive, now),
                hasZone: MagnetTime.hasZone(naive),
                offsetMs: MagnetTime.parse("2026-11-01T08:13:36+08:00"),
                diagnostics: MagnetTime.diagnostics.naiveAssumedUtc,
              };
            }
            """
        )
        await browser.close()

    assert result["hasZone"] is False
    assert result["naiveMs"] == result["utcMs"], "裸时间戳必须按 UTC 解释"
    assert result["relative"] == "8 分钟前", "8 分钟前不能再显示成 8 小时前"
    # 带偏移的照偏移解析：+08:00 的 08:13 就是 00:13Z
    assert result["offsetMs"] == int(
        datetime.fromisoformat("2026-11-01T00:13:36+00:00").timestamp() * 1000
    )
    assert result["diagnostics"] >= 1


@pytest.mark.asyncio
async def test_paused_is_a_state_not_a_failure(static_server_url):
    """N18：58 条 stoppedDL 不能显示成失败；要能筛出来、颜色是暂停色。"""
    async with async_playwright() as playwright:
        browser, page, _ = await _open(playwright, static_server_url)
        await page.evaluate(
            """
            () => {
              handleMsg({type: 'init', items: [
                {hash: 'P1', name: 'Paused one', status: 'paused',
                 torrent_state: 'stoppedDL', category: '电影', created_at: '2026-11-01T08:00:00Z'},
                {hash: 'E1', name: 'Broken one', status: 'error',
                 error_msg: 'qB 种子状态无法识别: stoppedDL', category: '电影',
                 created_at: '2026-11-01T08:01:00Z'},
                {hash: 'S1', name: 'Done one', status: 'success',
                 category: '电影', created_at: '2026-11-01T08:02:00Z'},
                {hash: 'U1', name: 'Weird one', status: 'weird_state',
                 category: '电影', created_at: '2026-11-01T08:03:00Z'},
              ]});
            }
            """
        )
        paused_badge = await page.locator("#row-P1 .badge").inner_text()
        paused_class = await page.locator("#row-P1 .badge").get_attribute("class")
        paused_sentence = await page.locator("#row-P1 .result-text").inner_text()
        unknown_badge = await page.locator("#row-U1 .badge").inner_text()
        unknown_class = await page.locator("#row-U1 .badge").get_attribute("class")
        unknown_sentence = await page.locator("#row-U1 .result-text").inner_text()
        counts = {
            "all": await page.locator("#totalCount").inner_text(),
            "paused": await page.locator("#pausedCount").inner_text(),
            "failed": await page.locator("#errorCount").inner_text(),
        }

        await page.locator('.stat[data-status="paused"]').click()
        paused_rows = await page.locator("#tbody tr").count()
        paused_titles = await page.locator("#tbody .item-name").all_inner_texts()
        hint = await page.locator("#statusFilterChip").inner_text()

        await page.locator('.stat[data-status="failed"]').click()
        failed_titles = await page.locator("#tbody .item-name").all_inner_texts()

        await page.locator('.stat[data-status="all"]').click()
        all_rows = await page.locator("#tbody tr").count()
        await browser.close()

    assert paused_badge == "已暂停"
    assert "badge-paused" in paused_class
    assert "在 qB 里点继续" in paused_sentence
    assert "stoppedDL" in paused_sentence
    assert unknown_badge == "未知状态"
    assert "badge-muted" in unknown_class
    assert "weird_state" in unknown_sentence
    assert counts == {"all": "4", "paused": "1", "failed": "1"}
    assert paused_rows == 1 and paused_titles == ["Paused one"]
    assert "已暂停" in hint and "1" in hint
    assert failed_titles == ["Broken one"]
    assert all_rows == 4


@pytest.mark.asyncio
async def test_reconnect_keeps_selection(static_server_url):
    """重连换了一份 init 快照，用户勾过的行不能被悄悄清掉。"""
    api = ApiMock()
    api.payloads["items"] = {
        "items": [
            {"hash": "A1", "name": "Alpha", "status": "pending", "category": "电影"},
            {"hash": "B1", "name": "Beta", "status": "pending", "category": "电影"},
        ],
        "total": 2,
    }
    async with async_playwright() as playwright:
        browser, page, _ = await _open(playwright, static_server_url, api=api)
        await page.evaluate(
            """
            () => {
              const items = [
                {hash: 'A1', name: 'Alpha', status: 'pending', category: '电影'},
                {hash: 'B1', name: 'Beta', status: 'pending', category: '电影'},
              ];
              handleMsg({type: 'init', items});
              toggleRow('A1', true);
              handleMsg({type: 'init', items});
            }
            """
        )
        await page.wait_for_timeout(250)
        checked = await page.locator("#row-A1 .row-checkbox").is_checked()
        selected = await page.locator("#selCount").inner_text()
        await browser.close()
    assert checked is True
    assert selected == "1"


@pytest.mark.asyncio
async def test_repeated_identical_failures_collapse_into_one_toast(static_server_url):
    """缺陷 ④：同一句报错刷八遍 → 一条通知 + ×8，且最多 3 条并存。"""
    async with async_playwright() as playwright:
        browser, page, _ = await _open(playwright, static_server_url)
        await page.evaluate(
            """
            () => {
              for (const text of ['第一件事', '第二件事', '第三件事', '第四件事']) {
                toast(text, 'error', {key: 'k:' + text});
              }
              for (let i = 0; i < 8; i += 1) toast('qBittorrent 配置持久化失败', 'error');
            }
            """
        )
        toasts = page.locator("#toastStack .toast")
        count = await toasts.count()
        collapsed = page.locator('#toastStack .toast[data-toast-key="error:qBittorrent 配置持久化失败"]')
        collapsed_count = await collapsed.locator(".toast-count").inner_text()
        await browser.close()
    assert count == 3, "通知最多 3 条并存"
    assert collapsed_count == "×8"


@pytest.mark.asyncio
async def test_readonly_mode_says_human_words_and_never_looks_like_qb_failure(
    static_server_url,
):
    """严格模式（未来外网）：页面只读、人话提示；读接口的 401 不是 qB 故障红。"""
    api = ApiMock()
    api.payloads["auth"] = {
        "mode": "strict",
        "trusted": False,
        "write": False,
        "required": True,
        "key": "missing",
        "reason": "strict_mode",
    }
    api.payloads["config"] = {
        "qbit_host": "http://192.168.20.193:8085",
        "qbit_username": "qb-user",
        "qbit_password_configured": True,
    }
    api.status_overrides = {
        "/api/v1/status": (401, {"detail": "Invalid or missing API key"}),
    }
    async with async_playwright() as playwright:
        browser, page, _ = await _open(playwright, static_server_url, api=api)
        qbit_text = await page.locator("#qbitStatus").inner_text()
        qbit_dot = await page.locator("#dotQbit").get_attribute("class")
        toast_count = await page.locator("#toastStack .toast").count()
        auth_text = await page.locator("#authState").inner_text()
        auth_class = await page.locator("#authState").get_attribute("class")
        page_text = await page.locator("body").inner_text()
        cfg_host = await page.locator("#cfgHost").input_value()
        await browser.close()

    assert "只读" in auth_text
    assert "muted" in (auth_class or "")
    assert "请求缺少 API Key" not in page_text
    assert "需要授权" in qbit_text
    assert "离线" not in qbit_text
    assert "offline" not in (qbit_dot or ""), "只读不该点红"
    assert toast_count == 0, "页面加载时的只读状态不该弹错误提示"
    # 读开放：只读设备也能看见真实 qB 地址（不是占位符），写才被挡住。
    assert cfg_host == "http://192.168.20.193:8085"


@pytest.mark.asyncio
async def test_ws_frame_carries_no_key_and_4401_stops_reconnect(static_server_url):
    """实时通道：浏览器不发 key、URL 不带凭据；严格模式 4401 后停止空转重连。"""
    async with async_playwright() as playwright:
        browser, page, _ = await _open(playwright, static_server_url)
        capture = await page.evaluate(
            """
            () => ({
              urls: window.__ws.urls.slice(),
              sent: window.__ws.sent.slice(),
            })
            """
        )
        await page.evaluate(
            """
            () => {
              window.__ws.last.onclose({code: 4401, reason: 'api_key_missing'});
            }
            """
        )
        await page.wait_for_timeout(600)
        status_text = await page.locator("#wsStatus").inner_text()
        status_dot = await page.locator("#dotWs").get_attribute("class")
        urls_after = await page.evaluate("() => window.__ws.urls.length")
        toast_text = await page.locator("#toastStack").inner_text()
        await browser.close()

    assert all("api_key" not in url for url in capture["urls"]), "key 不能进 URL"
    frames = [json.loads(item) for item in capture["sent"] if item.startswith("{")]
    assert any(frame.get("type") == "auth" for frame in frames)
    assert all("api_key" not in frame for frame in frames), "浏览器帧里不许出现 key"
    assert "只读" in status_text
    assert "offline" not in (status_dot or ""), "只读不点故障红"
    assert urls_after == 1, "只读后不该继续每 2 秒重连"
    assert "只读模式" in toast_text


@pytest.mark.asyncio
async def test_clear_dialog_states_scope_and_needs_explicit_confirmation(static_server_url):
    """缺陷 ⑥：清空必须说清作用域（整个库，不是当前筛选），且要点两下加勾选。"""
    api = ApiMock()
    async with async_playwright() as playwright:
        browser, page, _ = await _open(
            playwright, static_server_url, api=api
        )
        await page.evaluate(
            """
            () => handleMsg({type: 'init', items: [
              {hash: 'X1', name: 'One', status: 'success', category: '电影'},
              {hash: 'X2', name: 'Two', status: 'paused', category: '音乐'},
            ]})
            """
        )
        await page.locator("#clearBtn").click()
        dialog_open = await page.locator("#clearDialog").evaluate("(el) => el.open")
        copy = await page.locator("#clearDialog").inner_text()
        count_text = await page.locator("#clearCount").inner_text()
        confirm_disabled = await page.locator("#clearConfirmBtn").is_disabled()

        await page.locator("#clearConfirmCheck").check()
        confirm_enabled = await page.locator("#clearConfirmBtn").is_enabled()
        confirm_label = await page.locator("#clearConfirmBtn").inner_text()

        await page.locator("#clearConfirmCheck").uncheck()
        confirm_disabled_again = await page.locator("#clearConfirmBtn").is_disabled()

        await page.locator("#clearConfirmCheck").check()
        await page.locator("#clearConfirmBtn").click()
        await page.wait_for_timeout(200)
        delete_requests = [
            request for request in api.requests if request["method"] == "DELETE"
        ]
        dialog_closed = await page.locator("#clearDialog").evaluate("(el) => el.open")
        await browser.close()

    assert dialog_open is True
    assert count_text == "2"
    assert "整个资源库" in copy and "不是当前筛选结果" in copy
    assert "qBittorrent 里已有的种子不会被删除" in copy
    assert confirm_disabled is True
    assert confirm_enabled is True
    assert confirm_label == "清空全部 2 条"
    assert confirm_disabled_again is True
    assert len(delete_requests) == 1
    assert "scope=all" in delete_requests[0]["url"]
    assert dialog_closed is False


@pytest.mark.asyncio
async def test_save_config_reports_one_outcome_with_reason(static_server_url):
    """⑤/④：保存失败要说原因与下一步；成功只留一条通知。"""
    api = ApiMock()
    api.payloads["config_put"] = {"status": "failed", "connected": False}
    async with async_playwright() as playwright:
        browser, page, _ = await _open(playwright, static_server_url, api=api)
        await page.evaluate("() => saveConfig()")
        await page.wait_for_timeout(150)
        fail_text = await page.locator("#cfgResult").inner_text()
        fail_hint = await page.locator("#cfgHint").inner_text()
        fail_toasts = await page.locator("#toastStack .toast").count()

        api.payloads["config_put"] = {"status": "ok", "connected": True}
        await page.evaluate("() => saveConfig()")
        await page.wait_for_timeout(150)
        ok_text = await page.locator("#cfgResult").inner_text()
        ok_toasts = await page.locator("#toastStack .toast").all_inner_texts()
        await browser.close()

    assert "连不上 qBittorrent" in fail_text
    assert "qB 是否在运行" in fail_hint
    assert fail_toasts == 1, "一次保存只该有一条失败通知"
    assert "连接成功" in ok_text
    assert sum("配置已保存" in text for text in ok_toasts) == 1
