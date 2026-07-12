# Magnet Harvester 缺陷扫描报告

> 历史快照：本文记录的是迁移前的扫描结果，其中 crawl4ai、旧 crawler 内部方法和已删除
> 测试文件的描述不再代表当前实现。当前爬虫架构与验收依据见
> `docs/specs/scrapling-crawler-migration.md`，爬取调度现由 Scrapling Spider 负责。

> 扫描范围：`magnet_harvester/` 全量 Python 源码 + 配置 + 测试
> 扫描维度：Bug / 安全 / 性能 / 并发 / 架构 / 测试 / 部署
> 版本：v3.0.0 | 日期：2026-06-19 | 方法：codebase-memory-mcp 知识图谱查询 + Grep + 人工审查

---

## ✅ 已修复缺陷（此版本不再适用）

| 原编号 | 原问题                                                 | 修复状态                                                                                                              |
| ------ | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| P0-1   | InMemoryItemStore 字典迭代崩溃                         | ✅ 已使用 `list(self._items.values())` 快照                                                                           |
| P0-2   | crawler.py 共享 Set 竞态                               | ✅ 已改用 crawl4ai BFSDeepCrawlStrategy                                                                               |
| P0-3   | SSRF — 任意 URL 爬取                                   | ✅ 已实现 `utils/url_validator.py` 全量验证                                                                           |
| P0-4   | API 无认证保护                                         | ✅ 已实现 `utils/auth.py` require_api_key + 安全态势检查                                                              |
| P1-5   | qB 配置明文回传                                        | ✅ PUT /api/config 不再返回密码                                                                                       |
| P1-6   | WebSocket 无心跳                                       | ✅ 已添加心跳和重连机制                                                                                               |
| R-1a   | `crawler.py` 会话任务裸 `create_task` 和提前关闭不取消 | ✅ 已改用 `BGTaskManager.spawn()`，`crawl().aclose()` 会取消未完成 session                                            |
| R-1b   | `MessageBus` fan-out 裸 `asyncio.create_task`          | ✅ 已改用 `BGTaskManager.spawn()`，保留超时取消和异常隔离语义                                                         |
| S-1    | `try_decode_base64` 复杂度过高                         | ✅ 已拆为 Base64 候选迭代、单候选解码、磁力文本识别；结果稳定按首次出现顺序去重                                       |
| S-2    | `extract_from_text` 循环嵌套/重复解析流程              | ✅ 已拆为候选来源迭代器 + `_append_unique_magnet()` 去重解析                                                          |
| S-3    | `validate_crawl_url` 安全关键函数复杂度偏高            | ✅ 已复核为过期项；当前已拆出 `_validate_protocol()` / `_validate_hostname()` / `_is_unsafe_address()`                |
| S-4    | `classify_local` 循环内重复正则编译                    | ✅ 已预编译 `COMPILED_LOCAL_RULES`，分类入口 interface 不变                                                           |
| S-5    | `_json_serializer` 疑似死代码                          | ✅ 已复核为 codebase-memory 假阳性；函数通过 `json.dumps(default=_json_serializer)` 使用                              |
| R-2    | 静默异常吞没                                           | ✅ 已复核并修正剩余项；`magnet_parser` 不再包裹宽泛异常，`site_auth`/`qbit_client` 均记录异常后按容错 interface 降级  |
| R-3    | WebSocket 死连接清理边界案例                           | ✅ `_on_event` 发送前检查真实 `WebSocketState`，断开连接直接移除                                                      |
| P1-6   | qB 状态同步缺少错误恢复                                | ✅ 已添加 `SyncBackoffPolicy` 指数退避；qB snapshot 成功后才扫描 store                                                |
| P1-7   | Pipeline 下载阶段串行执行                              | ✅ 已复核为过期项；当前 `_download_items()` 使用 `asyncio.gather()` + `Semaphore` 限并发                              |
| P1-8   | MessageBus 超时任务异常未检索                          | ✅ 已复核为已修复；当前 `_EventDelivery` 使用 `gather(return_exceptions=True)` 并取消后再次 gather                    |
| P1-9   | `FS_BASE_PATH` 空值导致当前目录污染                    | ✅ 已复核为已修复；`MagnetSubmitter` 仅在 `fs_base_path` 非空时创建本地目录                                           |
| P1-10  | 磁力链接哈希格式校验不一致                             | ✅ 已修复；`MagnetSubmitter` 复用 `magnet_parser.HASH_RE`，提交层与提取层 btih 规则一致                               |
| P1-11  | 详情页链接模式过于僵化                                 | ✅ 已复核为已缓解；`DETAIL_URL_RE` 已覆盖 details/torrent/view/resource/movie/subject 和常见 id 查询参数              |
| P1-12  | qB 全局配置并发修改风险                                | ✅ 已修复；`QBitRuntime.replace_qbit_config()` 使用 runtime 级 lock 串行化 build/ping/persist/replace/commit          |
| P2-20  | crawler 重试延迟无抖动                                 | ✅ 已复核为过期项；当前无手写固定指数退避，重试交给 crawl4ai `max_retries`                                            |
| P2-14  | WebSocket 广播串行阻塞                                 | ✅ 已复核为已修复；当前 `_on_event` 对连接快照并发 `asyncio.gather()`                                                 |
| P2-15  | WebSocket 入站消息未处理                               | ✅ 已修复；`handle_client_message()` 解析控制消息，支持 ping/pong 心跳和错误响应                                      |
| P2-16  | `MagnetItem` 缺少时间戳字段                            | ✅ 已修复；模型包含 `created_at` / `updated_at`                                                                       |
| P2-17  | `InMemoryItemStore.list()` 全量排序分页                | ✅ 已修复；筛选候选以迭代器流过 `heapq.nsmallest()`，小 limit 不再全量排序                                            |
| P2-19  | 关键词前缀匹配过宽                                     | ✅ 已修复；短关键词统一走 token-boundary 正则，不再用裸 `startswith()`                                                |
| P2-21  | `ensure_category` 固定 sleep 且未验证创建结果          | ✅ 已修复；创建分类后轮询 qB 分类列表，超时仍不可见则返回失败                                                         |
| P2-22  | qB 登录/请求成功不重置失败计数                         | ✅ 已修复；transport 成功登录和成功请求会重置 `consecutive_failures` 并记录 `last_success_time`                       |
| P2-23  | fallback 默认分类为“电影”                              | ✅ 已修复；未命中规则时返回“其他”                                                                                     |
| P2-13  | `items_cleared` 使用错误事件类型                       | ✅ 已添加 `EventType.ITEMS_CLEARED` 并由 `MagnetItemTransitions.cleared()` 发射                                       |
| P2-18  | `ErrorHandler._cleanup_old_errors` 额外清理 100 条     | ✅ 已改为只清理超过 `_max_errors` 的旧记录                                                                            |
| P2-24  | Agent 手动分类路径设置错误                             | ✅ `UserActionExecutor.manually_reclassify()` 通过 `MagnetItemTransitions.manually_classified()` 同步分类与保存路径   |
| P2-25  | Agent depth 无上限                                     | ✅ 爬取入口集中到 `HarvestPipeline.start_crawl()`，统一执行 URL admit 和 depth 截断                                   |
| P3-26  | `settings.check_disk_space()` 不存在                   | ✅ 已修复；`Settings.check_disk_space()` 返回磁盘容量、阈值和低空间状态，lifespan 直接调用                            |
| P3-27  | `parse_magnet` 截断空格标题                            | ✅ 已修复；raw 清理不再用 `split()[0]`，保留 `dn` 中的字面空格                                                        |
| P3-28  | Base64 解码非法 UTF-8 静默丢字符                       | ✅ 已修复；严格 UTF-8 失败时用替换符保留字节位置并记录 debug                                                          |
| P3-29  | `CrawlerRunConfig.word_count_threshold=1` 过宽         | ✅ 已修复；新增 `CRAWLER_WORD_COUNT_THRESHOLD`，默认 10 并注入 crawl4ai run config                                    |
| P3-30  | `bus.py` 无意义 done callback                          | ✅ 已复核为过期项；当前 `MessageBus` 通过 `BGTaskManager.spawn()` 和 `gather(return_exceptions=True)` 管理任务        |
| P3-31  | qB 路径丢失前导斜杠                                    | ✅ 已复核为已修复；`_extract_base_from_path()` 返回带前导 `/` 的父路径                                                |
| P3-32  | `GET /api/config` 泄露 qB 用户名                       | ✅ 已修复；读取配置也走 `require_api_key`，API key 为空时仍保持开发兼容                                               |
| P3-33  | `start_crawl` 无任务追踪                               | ✅ 已修复；`BGTaskManager` 保留任务快照，`start_crawl` 返回 `task_id`，`/api/tasks/{task_id}` 可查询                  |
| P3-34  | `InMemoryItemStore.add_batch()` 无事务语义             | ✅ 已修复；先构造批次 pending map，全部通过后一次性提交，异常时不半写入                                               |
| P3-35  | 分类规则不支持热更新                                   | ✅ 已修复；`LocalClassifier.reload_rules()` 重载文件型关键词规则，`POST /api/classifier/reload` 触发                  |
| P3-37  | `pyproject.toml` 缺少 `playwright` 依赖声明            | ✅ 已复核为已修复；`project.dependencies` 已包含 `playwright>=1.40`                                                   |
| P3-38  | 仓库包含不应提交文件                                   | ✅ 已修复/复核；删除旧分析产物，清理本地垃圾并更新 ignore；`package-lock.json` 保留为 npm dev 工具锁文件              |
| P3-39  | `requirements.txt` 与 `pyproject.toml` 不同步          | ✅ 已修复；补齐 `requirements.txt` 的 `playwright` 和 `pyproject.toml` 的 `pyperclip`，并新增 manifest 同步测试       |
| P3-40  | 缺少集成测试                                           | ✅ 已修复；新增 `tests/integration/test_crawl_to_download_flow.py` 覆盖 REST crawl 到 pipeline/store/qB fake 的完整流 |
| P3-36  | `QBitSyncLoop` 轮询间隔不可配置                        | ✅ 已修复；`Settings.QBIT_SYNC_INTERVAL` 通过 assembly 注入 `QBitSyncLoop.poll_interval`                              |

---

## 🟢 当前有效缺陷复核

### 结构性缺陷

#### S-1: 已修复: `try_decode_base64` 复杂度过高

- **位置**: `magnet_harvester/magnet_parser.py` `try_decode_base64()`
- **状态**: 已拆为 `_iter_base64_candidates()`、`_decode_candidate()`、`_magnet_from_decoded_text()`；入口 `try_decode_base64(text)` 保持不变。
- **行为改进**: 结果按文本首次出现顺序稳定去重，不再使用 `set()` 返回无序列表。
- **验证**: `tests/test_magnet_extract.py::test_base64_decoding_preserves_first_seen_order_and_deduplicates`

#### S-2: 已修复: `extract_from_text` 循环嵌套过深

- **位置**: `magnet_harvester/magnet_parser.py` `extract_from_text()`
- **状态**: 已将标准 magnet、Base64、JSON/引号来源拆为 `_iter_*` 候选生成器，并通过 `_append_unique_magnet()` 集中解析和去重。
- **验证**: `tests/test_magnet_extract.py`、`tests/test_clipboard_monitor.py`、`tests/test_magnet_source_extractor.py` 覆盖主要提取路径。

#### S-3: 已复核: `validate_crawl_url` 安全关键函数复杂度偏高

- **位置**: `magnet_harvester/utils/url_validator.py` `validate_crawl_url()`
- **状态**: 旧扫描项已过期。当前代码已拆出 `_validate_protocol()`、`_validate_hostname()`、`_is_unsafe_address()`，DNS 和重定向验证集中在 `CrawlTargetAdmission`。
- **结论**: 无需继续拆分，当前 module interface 保持清晰。

#### S-4: 已修复: `classify_local` 循环内重复正则编译

- **位置**: `magnet_harvester/classifier/fallback.py` `classify_local()`
- **状态**: 已添加 `COMPILED_LOCAL_RULES`，规则在模块加载时编译一次；`classify_local(name)` interface 不变。
- **验证**: `tests/test_default_category.py`, `tests/test_classifier_rule.py`, `tests/test_classifier_keywords.py`, `tests/test_local_classifier.py`

#### S-5: 已复核: `_json_serializer` 疑似死代码

- **位置**: `magnet_harvester/api/websocket.py` `_json_serializer()`
- **状态**: codebase-memory 假阳性。函数作为 `json.dumps(default=_json_serializer)` 回调使用，静态调用图不追踪该模式。
- **结论**: 保留。

---

### 运行时缺陷

#### R-1: 已修复: 裸 `asyncio.create_task` 违反项目规范

- **位置**: `magnet_harvester/bus.py`, `magnet_harvester/crawler.py`, `magnet_harvester/services/qbit_sync.py`, `magnet_harvester/services/clipboard_monitor.py`
- **状态**: 后台任务创建已集中到 `BGTaskManager.spawn()` / `BGTaskManager.create()`；MessageBus fan-out 仍保留超时取消和异常隔离。
- **验证**: `tests/test_bus_memory_leak.py`, `tests/test_bus_backpressure.py`, `tests/test_crawler_detail_links.py` 覆盖慢订阅者取消和 crawler session 提前关闭。

#### R-2: 已复核: 静默异常吞没

- **位置**:
  - `magnet_harvester/magnet_parser.py` — 宽泛异常包裹已移除，单候选解码仅捕获 `binascii.Error` / `ValueError`
  - `magnet_harvester/services/site_auth.py` — JSON 和 URL 解析失败均记录日志后降级
  - `magnet_harvester/qbit_client/client.py` — qB 查询 facade 记录 warning/debug 后返回空对象，属于调用方可处理的容错 interface
- **结论**: 不再存在“静默”吞异常。qB 查询方法仍保留宽泛 `Exception`，是为隔离外部 qB/httpx 运行时失败；后续如要进一步深化，可独立提取 `QBitQueryGateway`。

#### R-3: 已修复: WebSocket 死连接清理边界案例

- **位置**: `magnet_harvester/api/websocket.py:84-88`
- **严重度**: 🟢 LOW
- **状态**: `_on_event` 发送前检查真实 `WebSocketState`；已断开的连接直接加入 dead 集合并移除，不再尝试 `send_text`。
- **验证**: `tests/test_websocket_broadcast.py::test_broadcast_skips_disconnected_clients` 覆盖断开连接跳过发送。

---

### 安全态势评估

| 维度             | 状态    | 说明                                                       |
| ---------------- | ------- | ---------------------------------------------------------- |
| API 写保护       | ✅ 强   | 所有写端点有 `Depends(require_api_key)`                    |
| SSRF 防护        | ✅ 强   | url_validator 阻断 loopback/link-local/multicast/RFC1918   |
| CORS             | ✅ 正确 | 默认禁用，仅在配置后启用                                   |
| 非 loopback 启动 | ✅ 正确 | `validate_security_posture()` 在无 API_KEY 时拒绝启动      |
| 密码存储         | ✅ 合理 | qB 密码在 .env 明文存储（此为 pydantic-settings 标准做法） |
| 默认密码         | 🟢 提示 | `admin:adminadmin` 为代码默认值，用户应在 .env 中覆盖      |
| Cookie 注入      | ✅ 安全 | 仅注入配置中显式指定的域名                                 |

**安全总评**: 项目安全态势良好，无不安全实践。

---

## 🔴 P0 — Critical（必须立即修复）

> ⚠️ 以下为旧报告内容，保留供历史参考。当前版本中这些问题已修复。

### 1. 已修复/复核: `InMemoryItemStore` 并发字典迭代崩溃风险

**位置**：`store.py:123-129`, `store.py:131-133`, `store.py:152-162`
**问题**：`list()`, `search()`, `stats()` 等方法在遍历 `self._items` 字典时，如果另一个协程同时调用 `add()` / `remove()` / `clear()`，会抛出 `RuntimeError: dictionary changed size during iteration`。
**复现场景**：WebSocket 广播器在 `_on_event` 中调用 `store.list()`，同时后台爬虫通过 `pipeline.execute()` 调用 `store.add()`。
**修复建议**：在 `InMemoryItemStore` 中添加 `asyncio.Lock`，或在遍历前复制字典视图：`list(self._items.values())` 已经是复制，但 `for item in self._items.values()` 在遍历过程中如果字典被修改仍会崩溃。应使用 `list(self._items.values())` 快照遍历（当前代码已使用，但 `stats()` 中也是 `for item in self._items.values()`，同样安全）。**实际上当前代码已使用 `list()` 包装，此问题不存在**。但 `clear()` 可能在 `list()` 调用和遍历之间执行，导致空列表。这不是崩溃，但可能丢失数据。
**重新评估**：当前 `list()` 和 `search()` 已使用 `list(self.__items.values())` 快照，不会崩溃。`stats()` 同样。但 `get_hashes_by_prefix()` 使用 `for h in self._items` 遍历键，如果同时 `clear()` 会崩溃。应改为 `list(self._items.keys())`。

### 2. 已修复: `crawler.py` 共享 Set 竞态条件导致重复爬取

**位置**：`crawler.py:344-350`
**问题**：`_claim_unvisited_links` 中 `if link in visited: continue; visited.add(link)` 不是原子操作。多个 worker 协程可能在同一时刻检查到 `link not in visited`，然后都将其加入，导致同一页面被重复爬取。
**修复建议**：使用 `asyncio.Lock` 保护 `visited` 和 `seen` 集合的读写，或改用 `asyncio.Queue` 的去重机制。

### 3. 已修复: `api/routes.py` SSRF 漏洞 — 任意 URL 爬取

**位置**：`api/routes.py:83-91`
**问题**：`start_crawl` 接口没有对 `req.url` 做任何验证，攻击者可以提交内网地址（如 `http://192.168.1.1:8080`、 `http://localhost:8085`、 `file:///etc/passwd`），导致服务端请求伪造（SSRF）。
**修复建议**：

1. 添加 URL 白名单 / 黑名单验证
2. 禁止内网 IP、localhost、文件协议
3. 限制只允许 http/https 协议

### 4. 已修复: `api/routes.py` 关键接口无认证保护

**位置**：`api/routes.py:83-91`, `api/routes.py:94-99`, `api/routes.py:102-105`, `api/routes.py:143-160`, `api/routes.py:163-168`
**问题**：`/api/crawl`, `/api/download`, `/api/reclassify`, `/api/config` (PUT), `/api/items` (DELETE) 等接口没有任何身份验证，任何能访问服务的人都可以：

- 向 qBittorrent 添加任意磁力链接
- 修改 qBittorrent 连接配置（包括密码）
- 清空所有采集数据
- 触发任意 URL 爬取
  **修复建议**：添加 API Key / Bearer Token / Basic Auth 中间件，至少对修改类接口进行保护。

### 5. 已修复: `main.py` CORS 配置过于宽松

**位置**：`main.py:47-49`
**问题**：`allow_origins=["*"]` 允许任何网站通过浏览器调用 API，配合无认证接口，攻击者可以通过构造恶意网页诱导用户触发爬取/下载操作。
**修复建议**：生产环境应设置为具体的域名白名单，或完全禁用 CORS（如果前端同域部署）。

---

## 🟠 P1 — High（严重影响功能或性能）

### 6. 已修复: `qbit_sync.py` 状态同步缺少错误恢复

**位置**：`services/qbit_sync.py:85-157`
**状态**：已添加 `SyncBackoffPolicy`，连续失败后从基础轮询间隔指数退避，最高到 `max_failure_backoff`。同步循环先拉取 qB snapshot，成功后才扫描 store 并 reconcile tracked items，避免 qB 离线时还对本地 store 做全量扫描。
**验证**：`tests/test_qbit_sync_loop.py::test_sync_backoff_policy_increases_after_failures_and_resets_on_success` 和 `tests/test_qbit_sync_loop.py::test_sync_failure_backs_off_without_scanning_store`。

### 7. 已复核: `pipeline.py` 下载阶段串行执行

**位置**：`pipeline.py:207-222`
**状态**：旧扫描项已过期。当前 `_download_items()` 使用 `asyncio.gather()` 并通过 `asyncio.Semaphore(concurrency)` 限制并发，默认并发 3。
**结论**：不再是串行下载缺陷。

### 8. 已复核: `bus.py` 超时任务异常未检索导致内存泄漏

**位置**：`bus.py:63-93`
**状态**：旧扫描项已过期。当前 `_EventDelivery.deliver()` 使用 `asyncio.gather(*tasks, return_exceptions=True)` 包住所有投递任务，超时后取消未完成任务并再次 `gather(return_exceptions=True)` 检索结果。
**结论**：不再存在未检索任务异常问题。

### 9. 已复核: `qbit_client/client.py` `FS_BASE_PATH` 空值导致当前目录污染

**位置**：`qbit_client/client.py:345-347`
**状态**：旧扫描项已过期。本地目录创建逻辑已移至 `MagnetSubmitter`，并且只在 `self._fs_base_path` 非空时执行 `Path(...).mkdir()`。
**结论**：空 `FS_BASE_PATH` 不会再创建当前工作目录下的分类目录。

### 10. 已修复: `qbit_client/client.py` 磁力链接哈希格式校验不一致

**位置**：`qbit_client/client.py:327` vs `magnet_parser.py:23-26`
**状态**：提交层已改为复用 `magnet_harvester.magnet_parser.HASH_RE`，与提取层统一接受 40 位 hex 或 32 位 Base32 btih，并拒绝 8 位等过短 hash。
**验证**：`tests/test_qbit_submitter.py::test_submitter_uses_parser_btih_validation_rules` 覆盖 8 位 btih 拒绝和 32 位 Base32 btih 允许。

### 11. 已复核: `crawler.py` 详情页链接硬编码模式过于僵化

**位置**：`crawler.py:330-332`
**状态**：旧扫描项已缓解。当前 `DETAIL_URL_RE` 已覆盖 `/details?/`, `/torrent/`, `/view/`, `/resource/`, `/movie/`, `/subject/`，以及 `id/tid/movie_id/detail` 查询参数。
**剩余空间**：如果后续要支持站点级自定义规则，可独立把 detail URL policy 抽成可配置 module。

### 12. 已修复: `config.py` 全局单例并发修改风险

**位置**：`config.py:135`
**状态**：配置变更已集中到 `QBitRuntime.replace_qbit_config()`，并新增 runtime 级 `config_lock`。`build_qbit_config`、新 client ping、`.env` 持久化、热替换和 `commit_qbit_config` 现在作为一个串行流程执行，避免两个 PUT `/api/config` 同时交错修改运行时和全局 settings。
**验证**：`tests/test_qbit_runtime_config.py::test_replace_qbit_config_serializes_concurrent_replacements` 覆盖并发替换不会重叠执行 qB ping。

---

## 🟡 P2 — Medium（影响体验或可维护性）

### 13. 已修复: `items_cleared` 错误事件类型滥用

**位置**：`magnet_harvester/transitions.py:225`
**状态**：`clear_items` 现在通过 `MagnetItemTransitions.cleared()` 广播 `EventType.ITEMS_CLEARED`，不再复用 `EventType.ERROR`。
**验证**：`tests/test_error_event_type.py` 和 `tests/test_api_auth.py` 覆盖该事件语义。

### 14. 已复核: `api/websocket.py` WebSocket 广播串行阻塞

**位置**：`api/websocket.py:71-76`
**状态**：旧扫描项已过期。当前 `_on_event` 对 `_active_ws` 快照使用 `asyncio.gather(..., return_exceptions=True)` 并发发送，并在发送前清理断开连接。
**结论**：不再是串行广播缺陷。

### 15. 已修复: `api/websocket.py` WebSocket 消息未处理

**位置**：`api/websocket.py:58-60`
**状态**：`handle_connection()` 现在把入站文本交给 `handle_client_message()` 处理。客户端发送 JSON `{"type": "ping"}` 或纯文本 `ping` 会收到 `{"type": "pong"}`；非法 JSON / 未支持的控制消息会收到错误响应，不再静默丢弃。
**验证**：`tests/test_websocket_broadcaster.py::test_handle_connection_replies_to_ping_message` 和 WebSocket focused suite。

### 16. 已修复: `models.py` `MagnetItem` 缺少时间戳字段

**位置**：`models.py:22-33`
**状态**：`MagnetItem` 已包含 `created_at: datetime = Field(default_factory=datetime.now)` 和 `updated_at: datetime = Field(default_factory=datetime.now)`。
**结论**：基础时间字段已具备；后续若要 TTL，需要另行设计更新时间维护策略。

### 17. 已修复: `store.py` `list()` 方法内存分页效率低

**位置**：`store.py:117-129`
**状态**：`list()` 现在通过 `_iter_filtered_items()` 流式筛选候选，再使用 `heapq.nsmallest(limit, ..., key=_item_name_key)` 取按名称排序的前 N 条。小 limit 查询不再构造完整排序列表，interface 和排序语义保持不变。
**验证**：`tests/test_store_protocol.py::test_list_uses_limited_top_n_selection` 和 store/API/qB sync focused suite。

### 18. 已修复: `errors.py` 错误清理逻辑计算错误

**位置**：`errors.py:111`
**状态**：`_cleanup_old_errors` 现在使用 `to_remove = max(0, len(self._errors) - self._max_errors)`，只删除超出上限的最旧记录。
**验证**：`tests/test_error_handler.py::test_cleanup_removes_only_entries_over_limit` 覆盖小容量上限场景。

### 19. 已修复: `keyword_recognizer.py` 前缀匹配过于宽泛

**位置**：`keyword_recognizer.py:47-49`
**状态**：已删除裸 `startswith()` 分支，所有关键词统一走 `_compile_keyword_patterns()` 生成的 token-boundary 正则。`AV.Movie`、`AV_Movie` 命中；`Avatar`、`Avengers` 不再误命中。
**验证**：`tests/test_classifier_keywords.py::test_short_keyword_requires_token_boundary`。

### 20. 已复核: `crawler.py` 重试延迟无抖动

**位置**：`crawler.py:289`
**状态**：旧扫描项已过期。当前 crawler 没有 `_fetch_with_retry` 手写 worker/retry loop；并发和重试由 crawl4ai deep crawl 配置处理，`CrawlerRunConfig.max_retries` 来自 `CrawlerConfig.max_retries`。
**验证**：`tests/test_retry_jitter.py` 覆盖当前 run config 使用 crawl4ai semaphore/deep crawl，而非手写 retry loop。

### 21. 已修复: `qbit_client/client.py` `ensure_category` 固定 sleep 不灵活

**位置**：`qbit_client/client.py:291-314`
**状态**：创建分类后不再固定 sleep 一次后直接返回成功；现在通过 `_wait_for_category()` 轮询 qB 分类列表。分类在轮询窗口内可见才返回成功，始终不可见则返回 False。
**验证**：`tests/test_qbit_categories.py::test_ensure_category_waits_until_created_category_is_visible` 和 `tests/test_qbit_categories.py::test_ensure_category_fails_when_created_category_never_appears`。

### 22. 已修复: `qbit_client/client.py` 登录成功不重置失败计数

**位置**：`qbit_client/_transport.py:70-160`
**状态**：`QBitTransport` 已集中通过 `_record_success()` / `_record_failure()` 维护健康计数。成功登录和成功请求都会重置 `consecutive_failures` 并记录 `last_success_time`，避免 qB 已恢复后 `is_healthy()` 仍因旧失败持续返回 False。
**验证**：`tests/test_qbit_transport.py::test_successful_request_resets_consecutive_failures`。

### 23. 已修复: `classifier/fallback.py` 默认分类为 "电影" 过于武断

**位置**：`classifier/fallback.py:27-33`
**状态**：`classify_local()` 未匹配任何规则时返回 `"其他"`。
**验证**：`tests/test_default_category.py` 覆盖默认分类行为。

### 24. 已修复: 手动分类路径设置错误

**位置**：`magnet_harvester/services/user_actions.py:65-75`, `magnet_harvester/transitions.py`
**状态**：旧 `services/agent_tools.py` 已移除。`UserActionExecutor.manually_reclassify()` 现在通过 `MagnetItemTransitions.manually_classified()` 应用分类变更，由 transition 统一维护分类与保存路径。
**验证**：`tests/test_agent_tool_path.py` 覆盖手动分类后的 `category` 和 `save_path`。

### 25. 已修复: Agent/HTTP 共用爬取入口 depth 无上限

**位置**：`magnet_harvester/pipeline.py:76-96`
**状态**：爬取入口集中到 `HarvestPipeline.start_crawl()`，执行 `max(1, min(int(depth), 3, self.max_crawl_depth()))` 后才创建后台任务。
**验证**：`tests/test_agent_depth_limit.py` 和 `tests/test_user_actions.py` 覆盖该路径。

---

## 🟢 P3 — Low（优化建议）

### 26. 已修复: `main.py` `check_disk_space` 方法不存在

**位置**：`main.py:34`
**状态**：`Settings.check_disk_space()` 已实现，使用 `FS_BASE_PATH`（为空时使用当前目录）调用 `shutil.disk_usage()`，返回 `total_gb` / `used_gb` / `free_gb` / `min_free_gb` / `low_space`。`main.lifespan()` 现在直接调用该 interface，不再用 `hasattr` 降级为空结果。
**验证**：`tests/test_config.py::test_check_disk_space_reports_configured_path` 和 config/lifespan focused suite。

### 27. 已修复: `magnet_parser.py` `parse_magnet` 截断风险

**位置**：`magnet_parser.py:73`
**状态**：`parse_magnet()` 已将 raw 清理集中到 `_clean_raw_magnet()`，不再用 `split()[0]` 把 `dn` 中的字面空格当作链接结束符。
**验证**：`tests/test_magnet_extract.py::test_parse_magnet_preserves_literal_spaces_in_dn`。

### 28. 已修复: `magnet_parser.py` Base64 解码后 UTF-8 丢失字符

**位置**：`magnet_parser.py:109`
**状态**：Base64 内容现在先按严格 UTF-8 解码；遇到非法字节时使用 `errors="replace"` 保留字节位置，并记录 debug 日志，不再用 `errors="ignore"` 静默丢字符。
**验证**：`tests/test_magnet_extract.py::test_base64_decoding_replaces_invalid_utf8_bytes_instead_of_dropping_them`。

### 29. 已修复: `crawler.py` `word_count_threshold=1` 过于宽松

**位置**：`crawler.py:276`
**状态**：已新增 `CrawlerConfig.word_count_threshold` / `Settings.CRAWLER_WORD_COUNT_THRESHOLD` / `.env.example` 配置项，默认值为 `10`。`MagnetCrawler._build_run_config()` 现在把配置值注入 `CrawlerRunConfig.word_count_threshold`，不再硬编码为 `1`。
**验证**：`tests/test_retry_jitter.py::test_run_config_uses_configured_word_count_threshold` 和 crawler/config focused suite。

### 30. 已复核: `bus.py` 无意义的 done_callback

**位置**：`bus.py:92`
**状态**：旧扫描项已过期。当前 `MessageBus` 不再添加 `lambda t: None`；事件投递任务通过 `BGTaskManager.spawn()` 创建，并由 `asyncio.gather(..., return_exceptions=True)` 统一检索结果和取消超时任务。
**结论**：无需代码变更。

### 31. 已复核: `qbit_client/paths.py` 路径丢失前导斜杠

**位置**：`qbit_client/paths.py:31`
**状态**：旧扫描项已过期。`_extract_base_from_path()` 已返回带前导 `/` 的父路径，并拒绝相对路径。
**验证**：`tests/test_path_leading_slash.py`。

### 32. 已修复: `api/routes.py` `get_config` 泄露敏感信息

**位置**：`api/routes.py:135-140`
**状态**：`GET /api/config` 现在和 `PUT /api/config` 一样依赖 `require_api_key`。当 `API_KEY` 配置存在时，未携带或携带错误 key 的请求返回 401；当 API key 为空时，仍保留本地开发向后兼容行为。
**验证**：`tests/test_api_auth.py::TestAPIKeyAuth::test_config_get_without_key_returns_401` 和 API/auth focused suite。

### 33. 已修复: `api/routes.py` `start_crawl` 无任务追踪

**位置**：`api/routes.py:83-91`
**状态**：`BGTaskManager.create()` 现在为后台任务分配 `task_id` 并保留运行/完成/失败/取消快照。`HarvestPipeline.start_crawl()` 返回该 `task_id`，`GET /api/tasks/{task_id}` 可查询任务状态；该查询同样走 `require_api_key`。
**验证**：`tests/test_bg_tasks.py::test_task_status_snapshot_lives_after_completion`、`tests/test_pipeline_phases.py::test_start_crawl_returns_trackable_task_id`、`tests/test_api_routes.py::test_task_status_route_uses_background_task_manager`。

### 34. 已修复: `store.py` `add_batch` 无事务语义

**位置**：`store.py:166-172`
**状态**：`add_batch()` 现在先构造待提交的 `pending` map，并在遍历完整个批次后一次性 `update()` 到 store。批次中出现异常时不会写入前半部分，重复 hash 仍按原语义只计一次新增。
**验证**：`tests/test_store_protocol.py::test_add_batch_does_not_partially_commit_when_batch_is_invalid` 和 store focused suite。

### 35. 已修复: `classifier/local_classifier.py` 规则不支持热更新

**位置**：`classifier/local_classifier.py:36-39`
**状态**：`KeywordRule` 现在支持文件型规则 reload，`LocalClassificationEngine.reload_rules()` 遍历规则链并重载可重载规则，`LocalClassifier.reload_rules()` 暴露稳定调用 interface。新增 `POST /api/classifier/reload` 通过 `AppContext.classifier` 触发热更新，并使用 `require_api_key` 保护。
**验证**：`tests/test_local_classifier.py::test_reload_rules_uses_updated_keyword_file` 和 `tests/test_api_routes.py::test_classifier_reload_route_uses_context_classifier`。

### 36. 已修复: `services/qbit_sync.py` 轮询间隔不可配置

**位置**：`services/qbit_sync.py:33`
**状态**：已添加 `Settings.QBIT_SYNC_INTERVAL`，`.env.example` 暴露默认值 `2.0`，`build_runtime()` 将配置值作为 `poll_interval` 注入 `QBitSyncLoop`。
**验证**：`tests/test_assembly_wiring.py::test_build_runtime_uses_configured_qbit_sync_interval` 和 `tests/test_appcontext.py::test_main_lifespan_supports_end_to_end_pipeline_flow`。

### 37. 已复核: `pyproject.toml` 缺少 `playwright` 依赖声明

**位置**：`pyproject.toml:14`
**状态**：旧扫描项已过期。`[project].dependencies` 已显式包含 `playwright>=1.40`。
**结论**：`pyproject.toml` 不再缺少该直接依赖；`requirements.txt` 同步问题仍由 P3-39 单独跟踪。

### 38. 已修复/复核: 仓库包含不应提交的文件

**位置**：根目录
**状态**：已删除旧生成产物 `architecture-review-20260619.html`、`codebase_analysis.md`、`plan.md`；已清理本地未跟踪垃圾 `.DS_Store`、`node_modules/`、`reasonix.toml`；`.gitignore` 新增 `reasonix.toml`、`.planning/`、`architecture-review-*.html`。`package-lock.json` 保留，因为项目使用 npm scripts / Husky / lint-staged / prettier，lockfile 有助于 dev 工具可重复安装。`.codebase-memory/` artifact 保留，因为本项目明确使用 codebase-memory 共享索引产物。
**验证**：`find . -maxdepth 2 ...` 不再发现这些本地垃圾和旧生成文件。

### 39. 已修复: `requirements.txt` 与 `pyproject.toml` 不同步

**位置**：`requirements.txt`
**状态**：两个运行时依赖清单已同步：`requirements.txt` 补齐 `playwright>=1.40`，`pyproject.toml` 补齐 `pyperclip>=1.9`。新增 `tests/test_dependency_manifest.py`，用 `tomllib` 对比两个清单的运行时依赖包名，防止再次漂移。
**验证**：`tests/test_dependency_manifest.py`。

### 40. 已修复: 测试文件缺少集成测试

**位置**：`tests/`
**状态**：已新增 `tests/integration/test_crawl_to_download_flow.py`，通过真实 FastAPI routes、`UserActionExecutor`、`HarvestPipeline`、`MagnetItemTransitions`、`InMemoryItemStore`、`BGTaskManager` 和 `ItemQueryExecutor`，配合 fake crawler/classifier/qB adapter，覆盖 `POST /api/crawl` 自动下载到 `GET /api/items` 可见 queued item 的完整纵向流程。
**验证**：`tests/integration/test_crawl_to_download_flow.py`。

---

## 缺陷统计

| 级别           | 数量   | 说明                           |
| -------------- | ------ | ------------------------------ |
| 🔴 P0 Critical | 5      | 安全漏洞、并发崩溃、SSRF       |
| 🟠 P1 High     | 7      | 性能瓶颈、错误恢复、数据不一致 |
| 🟡 P2 Medium   | 13     | 体验问题、可维护性、语义错误   |
| 🟢 P3 Low      | 15     | 优化建议、配置问题、代码风格   |
| **合计**       | **40** | —                              |

---

## 修复优先级建议

### 第一周（安全 + 稳定）

1. ✅ P0-3 SSRF 漏洞已修复（URL admission/validator）
2. ✅ P0-4 接口认证已修复（API Key / 安全态势检查）
3. ✅ P0-2 爬虫 Set 竞态条件已修复（crawl4ai BFS 策略 + 会话管理）
4. ✅ P0-5 CORS 配置已修复（默认禁用，仅允许配置来源）
5. 修复 P1-9 FS_BASE_PATH 空值保护

### 第二周（性能 + 可靠性）

6. 修复 P1-7 下载并发化（gather + Semaphore）
7. 修复 P1-6 同步循环优化（退避 + get_pending）
8. 修复 P1-8 Bus 内存泄漏（异常检索）
9. 修复 P1-10 哈希校验统一
10. 修复 P2-14 WebSocket 并发广播

### 第三周（体验 + 可维护性）

11. ✅ P2-13 错误事件类型滥用已修复
12. 修复 P2-16 时间戳字段
    12a. ✅ P2-18 错误清理逻辑已修复
13. ✅ P2-24 Agent 工具路径设置已修复
14. ✅ P2-25 Agent depth 限制已修复
15. 添加 P3-40 集成测试

---

_报告生成时间：2025-06-12_
_扫描工具：人工代码审查_
