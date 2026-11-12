# DNS TOCTOU 残差：准入解析与实际连接（#76）

- 日期：2026-11-12
- 地图：qb-nas 优化机会地图（2026-11）（#49）
- 票：#76（钉 IP 消除 DNS TOCTOU：把已准入地址固定到实际连接）
- 状态：**接受残差**。HTTP 钉 IP 技术上可行但代价过高；浏览器路径在现有单浏览器复用模型下不可行。
- 关联：#62 / #100（重定向逐跳准入已落地）、#58（文档基线）

## 问题

`CrawlTargetAdmission.admit()` 用 `getaddrinfo` 解析一次（`magnet_harvester/utils/url_validator.py:93-105`、`:118-139`），只返回原 URL 字符串，不返回解析到的地址。随后：

- HTTP 路径由 curl_cffi 在真正连接时重新解析（`magnet_harvester/page_fetch.py:325-346`，`session.get` 在 `:334`）；首跳和每一跳都一样。
- 浏览器路径由 Chromium 自己解析；`page.route` 只能拿到 URL，拿不到解析后的 IP（`magnet_harvester/page_fetch.py:488-515`）。

因此存在 DNS rebinding 窗口：admit 时返回公网地址通过校验，连接时返回内网地址。

## 取证

环境：curl_cffi 0.15.0、Scrapling 0.4.10、Playwright Chromium（本机 arm64）。本机 Python 3.15.2 下 curl_cffi 任何成功 HTTP 响应都会 SIGSEGV（exit 139），RESOLVE 验证改在 Python 3.13 的临时 venv 中执行，不影响结论。

### 1. curl_cffi / Scrapling

- `CurlOpt.RESOLVE` 有效：`Session(curl_options={CurlOpt.RESOLVE: ["pin.test:<port>:127.0.0.1"]})` 请求 `http://pin.test:<port>/x` 返回 200，服务端收到 `Host: pin.test:<port>`。**这证明可以钉 IP，且 SNI/Host 仍是原域名。**
- 但不能在复用 Session 上按请求改：`curl_cffi.requests.Session.request()` / `AsyncSession.request()` 没有 `curl_options` 参数，只有构造函数有；顶层 `curl_cffi.requests.request(curl_options=...)` 每次新建 Session（源码会 `with Session(...) as s`），等于每个请求一条新连接。
- Scrapling `FetcherSession` 不接受 `curl_options`；`_ASyncSessionLogic._merge_request_args`（`scrapling/engines/static.py:102-166`）只把 `follow_redirects` 映射成 `allow_redirects`，未知 kwargs 原样透传，但 curl_cffi 的 `Session.request` 不认 `curl_options`，会 TypeError。要走 Scrapling 只能用私有 `_async_curl_session` 注入，升级即坏。
- 可行但昂贵的方案：按 `(scheme, host, port, 已准入 IP)` 维护 pinned session 池，每个 session 构造时设 RESOLVE；跳转到新 host 就换池中 session。代价：admission 要返回地址、每个 origin 一条 TLS 连接、需处理池上限/空闲回收，且要么绕过 Scrapling 包装（丢 stealthy headers/Response 适配），要么依赖私有属性。
- 直接持有 curl handle 每请求 setopt 也不适合：本仓详情页并发抓取共享一个 HTTP session，改 handle 选项必须全局串行化。

### 2. 浏览器

- Chromium 启动参数 `--host-resolver-rules=MAP pin.test 127.0.0.1` 有效：本地 HTTP 服务收到 `Host: pin.test:<port>`，页面拿到 `pinned-browser`。
- 但该参数只在 `launch` 时静态生效；`AsyncDynamicSession` 全进程复用一个浏览器，无法按导航动态改解析。按 origin 重启浏览器才能钉 IP（每站数百毫秒、额外内存），且会打断 `max_pages` 复用、cookie 上下文与 `page.route` 准入。
- 代理方案也绕不开：HTTPS 经 CONNECT 后由浏览器做 TLS，代理替换目标 IP 会触发证书域名不匹配，除非 MITM，成本与风险都更高。Playwright `route` 只能按 URL 放行/拦截，看不到解析后地址。

### 3. 判定

| 路径 | 能否把 admit 的 IP 钉到连接 | 结论 |
| --- | --- | --- |
| HTTP（curl_cffi） | 能，per-origin pinned session 或每请求新建 session | 可行，但改动跨 admission/fetcher，连接复用与 Scrapling 包装受损 |
| 浏览器（Chromium） | 仅启动参数，按 origin 重启才能动态钉 | 不可行（现有单浏览器模型下） |
| 换传输层（httpx/自定义 transport） | 能（自定义 resolver/transport） | 失去 curl_cffi impersonate 指纹，爬虫成功率会降 |

### 4. 最小验证命令（可复跑）

curl_cffi（Python 3.13 + curl_cffi 0.15.0）：

```python
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from curl_cffi import requests
from curl_cffi.const import CurlOpt

seen = []
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        seen.append(self.headers.get("Host"))
        self.send_response(200); self.end_headers(); self.wfile.write(b"pinned")
    def log_message(self, *a): pass

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()
s = requests.Session(curl_options={CurlOpt.RESOLVE: [f"pin.test:{port}:127.0.0.1"]})
r = s.get(f"http://pin.test:{port}/x", timeout=5)
print(r.status_code, r.text, seen)  # 200 pinned ['pin.test:<port>']
```

Chromium：

```python
# Playwright async；launch(args=["--host-resolver-rules=MAP pin.test 127.0.0.1"])
# page.goto(f"http://pin.test:{port}/x") → 200，服务端 Host 为 pin.test:<port>
```

## 决策

**接受残差，不在本轮实现钉 IP。**

理由：

1. 触发条件受限：攻击者必须控制被抓取的域名，并让 DNS 在 admit 与连接两次查询间切换答案（第一次全是公网、第二次含内网）。普通站点/正常 DNS 不触发。
2. 影响限于服务端发起的 HTTP(S) 请求：响应会进入后续解析与入库，但不提供任意协议或任意命令执行；是否可读取决于内网服务是否响应。
3. 现有部署默认局域网、写接口有鉴权；把抓取入口暴露给不可信用户时，才需要把该风险当阻断项。
4. 实现代价与回归面（per-origin session 池、admission 返回地址、Scrapling 私有 API）明显大于当前收益；换 httpx 会牺牲 impersonate，属于另一条产品取舍。

## 残余与触发条件（写给运维/后续开发）

- **仍然存在**：`admit` 的 DNS 结果与实际连接不是同一地址；HTTP 与浏览器路径都一样。`follow_redirects=False` 只保证「每一跳请求前重新准入」，不保证「连接目标就是准入的 IP」。
- **触发条件**：可控域名 + DNS rebinding；或本地 DNS 被篡改/劫持后返回内网地址。
- **不建议**：把 `ALLOW_FAKE_IP` 当成通用放行；它只用于 mihomo/Clash fake-IP 部署，扩大的是攻击面。
- **部署侧缓解**：抓取容器/主机限制出站到内网管理网段；不要把服务暴露给不可信用户；确需暴露时先做网络层 egress 策略。
- **代码位置**：`magnet_harvester/utils/url_validator.py:93-139`（解析与准入）、`magnet_harvester/page_fetch.py:325-346`（HTTP 每跳）、`:488-515`（浏览器每 origin）。

## 后续若要真做

优先做 HTTP 路径，且先改 admission 契约：`admit()` 返回 `(url, addresses)`（或新增 `resolve_for_connection()`），并缓存「host → 本次准入地址」。然后：

1. `PageFetcher` 按 `(host, port, ip)` 维护有上限的 pinned session 池（LRU，空闲关闭）；
2. 每跳请求命中池；redirect 到新 host 时先 admit 再取/建 session；
3. 测试用计数 resolver 与本地监听断言连接确实落在准入 IP；
4. 浏览器路径单独评估：按 origin 启停浏览器或引入受控 DNS 代理，不要假装 `route` 能钉 IP。

在此之前，这份文档就是该风险的记录位置。
