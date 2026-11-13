# HTTP 重定向逐跳准入：决策笔记（#62）

- 日期：2026-11-09
- 地图：qb-nas 优化机会地图（2026-11）（#49）
- 票：修复 HTTP 重定向整链的事后校验（盲 SSRF）（#62）
- 状态：决议已定（本笔记即实现合同）；按地图约定本轮只规划，不改业务代码
- 关联：新增研究票「钉 IP 消除 DNS TOCTOU：把已准入地址固定到实际连接（HTTP/浏览器）」（#76）

## 问题

`page_fetch.py:167-176` 的 `FetcherSession(..., follow_redirects=True)` 让 curl 自己跟随整条重定向链；`_http_fetch` 只在响应回来后对 `last.url` 补一次 admit（`page_fetch.py:298-310`）。302 指向内网时，请求已经发出、响应体已经读进内存——准入退化成事后告警。`url_validator.admit_redirect_chain`（`url_validator.py:170-183`）本来就是逐跳预探，但生产零调用。

## 关键证据

- scrapling 0.4.10 `FetcherSession.__init__` 的默认值是 `follow_redirects="safe"`，文档原文：follows redirects but rejects those targeting internal/private IPs (SSRF protection)。项目显式传 `True`，等于关掉了依赖自带的保护。
- scrapling `_merge_request_args`（`engines/static.py:102-166`）会把 `follow_redirects` 映射成 `allow_redirects`、`max_redirects` 直传；未知 kwargs 原样进 `final_args`。
- curl_cffi 0.15.0：`allow_redirects="safe"` 映射为 `CurlFollow.SAFE`（`const.py:612-627`、`requests/utils.py:563-569`），策略由原生 libcurl 判定，黑盒：无法表达本仓的 `allow_fake_ip`（198.18.0.0/15），给不出 `FailureReason` 与中文文案，测试也无法注入 resolver。
- 全仓 `follow_redirects` 只有 `page_fetch.py` 一处是 HTTP 抓取路径；另两处在 `url_validator` 的 HEAD 探针。

## 方案比较

- **A（选中）**：`follow_redirects=False`，由 `PageFetcher` 逐跳发 GET；每跳发出前 `await admit(target)`。准入与抓取在同一条请求路径上，测试可注入 resolver，失败原因与文案可控。
- **B（否决）**：只删掉 `follow_redirects=True` 用依赖默认 `"safe"`。一行，但黑盒策略、无法表达 fake-IP 放行、仍要对终点自己 admit。
- **C（否决）**：保留自动跟随，请求前跑 `admit_redirect_chain`。每跳多一次 HEAD（对 HEAD 405/501 的站点失效），GET 链仍会重新解析，等于两次 DNS、两次机会。

## 选定的合同

`_default_http_factory` 改 `follow_redirects=False`。`_http_fetch` 的 attempt 循环包住整条链，重试从原 URL 重来；链内逐跳：

```python
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

async def _fetch_hop_chain(self, url, started):
    current, redirects = url, 0
    while True:
        await self._limiter.wait((urlparse(current).hostname or "").lower())
        try:
            session = await self._ensure_http()
            self.requests_sent += 1
            response = await session.get(current, timeout=self._config.timeout)
        except Exception as exc:
            return self._failure_from_exception(current, exc, started)

        status = int(getattr(response, "status", 0) or 0)
        headers = getattr(response, "headers", None) or {}
        location = headers.get("location") or headers.get("Location")

        if status in _REDIRECT_STATUSES and location:
            if redirects >= _MAX_REDIRECTS:
                return page_failure(current, status, FailureReason.HTTP_ERROR,
                                    f"重定向次数超过上限: {_MAX_REDIRECTS}")
            target = urljoin(current, location)
            if self._target_admission is not None:
                try:
                    await self._target_admission.admit(target)
                except Exception as exc:
                    return page_failure(target, status, FailureReason.UNSAFE_URL,
                                        f"重定向到不安全地址: {exc}"[:300])
            redirects += 1
            current = target
            continue

        if status in _REDIRECT_STATUSES:
            return page_failure(current, status, FailureReason.HTTP_ERROR,
                                f"HTTP {status} 缺少 Location")

        return self._page_from_response(current, response, started)
```

失败映射：

| 情况                | reason                                                                    | message                                    |
| ------------------- | ------------------------------------------------------------------------- | ------------------------------------------ |
| 下一跳 admit 失败   | `UNSAFE_URL`                                                              | `重定向到不安全地址: <原因>`（沿用现格式） |
| 超过 5 次重定向     | `HTTP_ERROR`                                                              | `重定向次数超过上限: 5`                    |
| 3xx 但无 Location   | `HTTP_ERROR`                                                              | `HTTP <status> 缺少 Location`              |
| 任一跳超时/连接失败 | 沿用 `_failure_from_exception`（TIMEOUT / HTTP_ERROR），整链从原 URL 重试 | 不变                                       |

不变式：

- 每一跳出站前都过 `admit`；`UNSAFE_URL` 不在浏览器升级清单内，不会绕道浏览器。
- `requests_sent` 改为按跳计数；`elapsed_ms` 每 attempt 从链首计时（整条链耗时）。
- `PageFetch.url` 继续记终点；`via` 仍为 `"http"`。
- 浏览器路径不改：`_guard_page_setup`（`page_fetch.py:418-435`）已经逐子请求 admit。

## 要删的死代码（改完后）

`admit_redirect_chain`、`_probe_redirect`、`_default_probe`、`CrawlTargetAdmission._client`/`close()`、`REDIRECT_PROBE_TIMEOUT_SEC`、`redirect_probe` 参数、`max_redirects` 参数，以及随之不再使用的 `urljoin` / `httpx` / `RedirectProbe` 导入。`crawler.py:186-188` 用 `hasattr(..., "close")` 守卫，删除 `close()` 不会炸。对应删掉 `tests/test_url_validator.py:119-120`、`:146-161`、`:164-175`、`:178-197` 四个测试。

## 测试计划（离线、可跑）

1. 重写 `tests/test_page_fetch.py:259-278`：302 Location=`http://127.0.0.1:9/secret`，断言 `FakeHttpSession.calls` 里没有第二跳、`reason=TIMEOUT/UNSAFE_URL`（按注入的 admission 异常），message 含 `重定向到不安全地址`。
2. 新增：相对 Location（urljoin 后请求）、跨站 302（新 host 被 admit 且真的被请求）、https→http 降级放行、5 跳上限（第 6 次请求不发生）、3xx 无 Location、跳链中 500 从原 URL 重试、`requests_sent` 按跳计数。
3. `python -m pytest tests/test_page_fetch.py tests/test_url_validator.py -q`，再跑全量；无新增依赖。

## 已知残余（写进票面，不假装关闭）

- **DNS TOCTOU 未消除**：admit 的解析与实际连接不是同一个地址；重定向与首跳都一样。另立研究票 #76。
- **并发槽只锁首跳**：`fetch()` 的 `_slot(host)` 用的是原始 host，跨站 302 的目标站只过 limiter、不占该站并发槽。
- **指标口径变化**：`requests_sent` 从「每 attempt 一次」变成「每跳一次」，`crawl_metrics` 消费方需知会。

## 环境限制

本机 `.venv`（Python 3.15.2 + curl_cffi 0.15.0，arm64）在第一个真实 HTTP 请求处 SIGSEGV（EXIT=139，无 stderr；纯 AsyncSession 与 FetcherSession 都一样），不是架构不匹配。因此本票验证只用离线假会话；真实站点复验放到 NAS 环境做，属于残余。

## 实现时还要改的文档

- `docs/specs/2026-11-01-http-first-crawl.md:18`：「跟随重定向后重新做 SSRF 准入」→「重定向逐跳，每跳发出前准入」。
- `docs/specs/2026-11-01-http-first-crawl.md:30` 同步措辞。
