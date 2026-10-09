# 验证层次与验收边界

本文档明确区分两层验证：**自动化测试通过** 与 **生产链路已验证**。
两者不能互相替代 —— “pytest 全绿”只说明代码逻辑符合预期，不证明
真实站点、真实 qBittorrent、NAS 下载链路可用。

## 1. 自动化测试（pytest，默认门禁）

覆盖范围：

- 假 qB（FakeQbit）、模拟爬虫、NullBus/RecordingBus 下的单元与集成测试
- URL 校验 / SSRF 防护、分类规则链、状态转换与事件、qB 客户端各模块
  （transport、mapper、paths、submitter、sync、stats）
- API 认证与路由、WebSocket 广播与握手认证、剪贴板监控

**能证明**：逻辑正确性、边界处理、并发与回滚语义、协议字段完整性。

**不能证明**：真实站点可抓、真实 qB 可登录、动态页面渲染、Cookie 注入
生效、NAS 磁盘与下载链路可用、网络延迟/超时下的真实表现。

运行方式：

```bash
python -m pytest tests -q        # 全量
ruff check magnet_harvester tests
```

## 2. 生产链路 smoke 验证（可选，真实环境）

`scripts/smoke_production.py` 针对真实环境执行受控检查：

| 步骤            | 验证内容                                     | 是否写副作用                                                |
| --------------- | -------------------------------------------- | ----------------------------------------------------------- |
| qB 登录         | 真实 WebUI 认证（ping）                      | 否                                                          |
| qB 分类 API     | 读取真实分类列表                             | 否                                                          |
| qB torrent 列表 | 读取真实 torrent 快照                        | 否                                                          |
| 站点抓取        | Scrapling 抓取真实页面并提取 magnet          | 否（仅浏览器访问）                                          |
| 本地分类        | 对抓取到的名称跑真实规则链                   | 否                                                          |
| 提交链路        | add_magnet + 状态轮询（仅 `SMOKE_SUBMIT=1`） | **是**：创建 smoke_test 分类并提交 magnet，可能真实写入磁盘 |

用法：

```bash
SMOKE_CRAWL_URL="https://example-site/page" \
SMOKE_QBIT_HOST="http://192.168.1.100:8080" \
SMOKE_QBIT_USERNAME="admin" \
SMOKE_QBIT_PASSWORD="****" \
  python scripts/smoke_production.py

# 如需同时验证真实提交（谨慎：会向 qB 添加 magnet）
SMOKE_SUBMIT=1 SMOKE_QBIT_... python scripts/smoke_production.py
```

可选变量：`SMOKE_SITE_COOKIES='{"domain": "cookie-string"}'` 注入 Cookie；
`SMOKE_CRAWL_TIMEOUT=120` 调整抓取超时（默认 60s）。

注意：smoke 使用的 magnet 是占位 hash（仅验证提交与轮询机制，无真实对等体）。
如需真实下载验证，替换脚本中 `_PLACEHOLDER_MAGNET` 为有效 magnet 后再运行。

## 3. WebSocket API Key 的已知权衡

- `/ws` 的 API Key 通过查询参数传递（浏览器 WebSocket 无法自定义请求头）。
  应用自身日志已脱敏：`uvicorn_log_config()` 给 access 与 default 两个 formatter
  都套了脱敏版，HTTP access log 与 WebSocket 握手行（后者由 uvicorn.error /
  default formatter 输出）中的 `api_key=` 值都会写成 `***`。
  **仍存在的外部暴露面**：反代（nginx/traefik 等）自己的 access log 在应用之外
  记录原始请求行。非本机部署时需在反代层重写/脱敏查询串，或将 key 视为短期
  凭据并定期轮换。
- 认证在 `ws.accept()` 之前完成，失败连接（4401）不进入广播器；
  比较使用 `secrets.compare_digest`（恒定时间），拒绝日志不含 key 内容。
- 未配置 `API_KEY` 时 `/ws` 保持开放（本地回环默认部署兼容模式）；
  非本机部署必须配置 `API_KEY`，否则资源名称/来源/下载状态可被未授权订阅。

## 3.1 启动方式与鉴权强制

无鉴权（未配置 `API_KEY` 且未设 `ALLOW_INSECURE_WRITE_API=true`）时，写操作有两层防护：

1. **启动期**：校验**真实绑定地址**，非 loopback 即拒绝启动。真实地址按优先级取自
   启动入口设置的 `MH_BOUND_HOST`、uvicorn CLI 的 `--host` 参数、`UVICORN_HOST`。
2. **请求期兜底**：来自**非 loopback 来源**的无鉴权写请求一律 403；WebSocket
   握手同样处理。判定同时看 ASGI scope 的两侧，缺一不可：
   - `server` —— 该连接被接受的**本机接口地址**。实测绑定 `0.0.0.0` 时经 loopback
     为 `127.0.0.1`、经 LAN 为真实网卡 IP，用于识别"直接经对外网卡进来"。
   - `client` —— 连接来源地址。**本机反向代理**（nginx/traefik 反代到 `127.0.0.1`
     后端）会让 `server` 恒为 `127.0.0.1`，此时只有 `client` 反映真实来源。

   只看 `server` 会漏掉反代形态（实测：反代转发远程请求时后端仍观测到
   `server=('127.0.0.1', ...)`）；只看 `client` 则漏掉直连形态。两侧都是 loopback
   才算本机访问。该判定与启动方式无关。

   已知边界：`client` 会被 uvicorn 按 `X-Forwarded-For` 重写（实测），而该头部可被
   客户端伪造。伪造风险被 `server` 一侧限制——想同时让 `server` 为 loopback，攻击者
   必须先在本机内发起连接。因此本项属**尽力而为的兜底**，不能替代 `API_KEY`。

两层的意义不同：第 1 层给出清晰的启动失败信息；第 2 层即使有人用非受支持的启动
方式（如 `uvicorn magnet_harvester.main:app --host 0.0.0.0`）也无法从 LAN 写操作。
本机 `127.0.0.1` 访问不受影响（本机浏览器改用 `localhost` 而非 LAN IP），读接口不
做限制，配置了 `API_KEY` 则两层都不介入。

仍推荐用 `python run.py` 启动。

## 3.2 SSRF 防护的已知残余风险（TOCTOU / DNS rebinding）

`CrawlTargetAdmission.admit()`（`magnet_harvester/utils/url_validator.py:153-168`）
解析主机名并校验**全部**解析结果，但返回的是**原 URL**，连接时必然再次解析：

```python
addresses = await self._resolver(parsed.hostname or "", port)   # :159 校验用解析
...
return candidate                                               # :168 连接时再解析一次
```

因此存在经典的 check-then-connect 窗口：目标域名首次解析为公网地址通过校验，
真正建连前再解析为 `127.0.0.1`/内网即可绕过。

- 已验证的缓解：`scrapling_spider.py` 安装的 `page.route` 逐请求复检**确实生效**
  （scrapling 安装版 `_controllers.py:346-350` 会消费 `page_setup`），且 SSRF 的
  常规绕过写法（十进制 `2130706433`、`0x7f.0.0.1`、`127.1`、`[::ffff:127.0.0.1]` 等）
  在 `admit()` 均被拒。
- 但该复检同样是 check-then-connect（校验用解析 ≠ Chromium 建连用解析），且
  robots / 静态 fetcher 路径不经过 `page.route`。
- `admit_redirect_chain`（重定向链全链校验）**当前无生产调用方**，仅有测试引用
  （`tests/test_url_validator.py:161,175,195`），因此重定向链校验实际未接线。

**彻底修法**（未实施，需改动连接层）：解析一次后把 IP 固定用于连接 —— httpx 自定义
transport，或 Chromium 的 `--host-resolver-rules`；也可在建连后校验对端 IP。
未实施的原因是它改动爬虫的连接与浏览器启动参数，属架构改动而非局部修补。

## 4. 验收结论判定

| 状态                      | 判定                                                                            |
| ------------------------- | ------------------------------------------------------------------------------- |
| pytest 全绿               | 逻辑与协议正确，**可以合入代码**，不能作为生产验收                              |
| smoke 全 PASS（不含提交） | 真实登录、抓取、分类链路可用                                                    |
| smoke 全 PASS（含提交）   | 真实提交与状态轮询链路可用                                                      |
| 生产验收（真机）          | 需在 NAS 上完成：真实下载落盘、断电恢复、长时间运行、磁盘/流量/错误注入后再判定 |

任何情况下，不得把“测试通过”“ERC/DRC 为 0”或“smoke PASS”描述为
“生产链路已完成验收”。
