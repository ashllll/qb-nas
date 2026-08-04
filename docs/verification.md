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
  后果：**反代/Uvicorn 访问日志会记录完整请求行（含 key）**。
  缓解：非本机部署时，在反代层重写/脱敏 access log 的查询串；
  或将 key 视为短期凭据并定期轮换。
- 认证在 `ws.accept()` 之前完成，失败连接（4401）不进入广播器；
  比较使用 `secrets.compare_digest`（恒定时间），拒绝日志不含 key 内容。
- 未配置 `API_KEY` 时 `/ws` 保持开放（本地回环默认部署兼容模式）；
  非本机部署必须配置 `API_KEY`，否则资源名称/来源/下载状态可被未授权订阅。

## 4. 验收结论判定

| 状态                      | 判定                                                                            |
| ------------------------- | ------------------------------------------------------------------------------- |
| pytest 全绿               | 逻辑与协议正确，**可以合入代码**，不能作为生产验收                              |
| smoke 全 PASS（不含提交） | 真实登录、抓取、分类链路可用                                                    |
| smoke 全 PASS（含提交）   | 真实提交与状态轮询链路可用                                                      |
| 生产验收（真机）          | 需在 NAS 上完成：真实下载落盘、断电恢复、长时间运行、磁盘/流量/错误注入后再判定 |

任何情况下，不得把“测试通过”“ERC/DRC 为 0”或“smoke PASS”描述为
“生产链路已完成验收”。
