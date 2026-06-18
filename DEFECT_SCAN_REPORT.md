# Magnet Harvester 缺陷扫描报告

> 扫描范围：`magnet_harvester/` 全量 Python 源码 + 配置 + 测试
> 扫描维度：Bug / 安全 / 性能 / 并发 / 架构 / 测试 / 部署
> 版本：v3.0.0 | 日期：2026-06-19 | 方法：codebase-memory-mcp 知识图谱查询 + Grep + 人工审查

---

## ✅ 已修复缺陷（此版本不再适用）

| 原编号 | 原问题 | 修复状态 |
|---|---|---|
| P0-1 | InMemoryItemStore 字典迭代崩溃 | ✅ 已使用 `list(self._items.values())` 快照 |
| P0-2 | crawler.py 共享 Set 竞态 | ✅ 已改用 crawl4ai BFSDeepCrawlStrategy |
| P0-3 | SSRF — 任意 URL 爬取 | ✅ 已实现 `utils/url_validator.py` 全量验证 |
| P0-4 | API 无认证保护 | ✅ 已实现 `utils/auth.py` require_api_key + 安全态势检查 |
| P1-5 | qB 配置明文回传 | ✅ PUT /api/config 不再返回密码 |
| P1-6 | WebSocket 无心跳 | ✅ 已添加心跳和重连机制 |

---

## 🟡 当前有效缺陷

### 结构性缺陷

#### S-1: `try_decode_base64` 复杂度过高 (C=11)
- **位置**: `magnet_harvester/magnet_parser.py` `try_decode_base64()`
- **严重度**: 🟡 MEDIUM
- **度量**: 复杂度 11 | 认知复杂度 30 | 38 行 | linear_scan_in_loop=1
- **问题**: 函数职责过多（Base64 解码 + 多编码尝试 + 磁力验证），循环内执行字符串搜索导致二次复杂度
- **建议**: 拆分为独立函数：`_decode_base64_variants()` + `_is_valid_magnet()`

#### S-2: `extract_from_text` 循环嵌套过深 (C=10)
- **位置**: `magnet_harvester/magnet_parser.py` `extract_from_text()`
- **严重度**: 🟡 MEDIUM
- **度量**: 复杂度 10 | 认知复杂度 22 | 40 行 | loop_count=6
- **问题**: 6 层循环嵌套使测试和维护困难，单一失败点影响所有提取路径
- **建议**: 提取内部解析逻辑到独立生成器函数

#### S-3: `validate_crawl_url` 安全关键函数复杂度偏高 (C=10)
- **位置**: `magnet_harvester/utils/url_validator.py` `validate_crawl_url()`
- **严重度**: 🟡 MEDIUM
- **度量**: 复杂度 10 | 25 行
- **问题**: 安全关键函数不应有高复杂度——每个分支都是潜在攻击面
- **建议**: 拆分为 `_validate_protocol()` + `_validate_hostname()` + `_validate_ip()`

#### S-4: `classify_local` 循环内线性扫描
- **位置**: `magnet_harvester/classifier/fallback.py` `classify_local()`
- **严重度**: 🟢 LOW
- **度量**: linear_scan_in_loop=1 | 复杂度 2
- **问题**: 规则匹配循环内执行 `in` 操作，规则数量目前很少，但会随规则增长而恶化
- **建议**: 对于大量规则场景，考虑编译正则或使用 trie 结构

#### S-5: `_json_serializer` 疑似死代码
- **位置**: `magnet_harvester/api/websocket.py` `_json_serializer()`
- **严重度**: 🟢 LOW
- **度量**: in_degree=0 | out_degree=0 | 4 行
- **问题**: 知识图谱显示零调用方，仅在 `json.dumps(default=...)` 中作为回调传递，图不追踪此模式
- **建议**: 如确实未使用则删除；如使用则保留（可能是 CBM 假阳性）

---

### 运行时缺陷

#### R-1: 裸 `asyncio.create_task` 违反项目规范 (4 处)
- **位置**:
  - `magnet_harvester/crawler.py:237`
  - `magnet_harvester/bus.py:62`
  - `magnet_harvester/services/qbit_sync.py:55`
  - `magnet_harvester/services/clipboard_monitor.py:67`
- **严重度**: 🟡 MEDIUM
- **问题**: 项目 AGENTS.md 明确要求"使用 `BGTaskManager.create()`，禁止裸 `asyncio.create_task`"。这些任务未注册到统一的异常监控和关闭生命周期中。
- **建议**:
  - `crawler.py:237` — 爬虫 session 任务应通过 BGTaskManager 创建
  - `bus.py:62` — 可接受（fire-and-forget + `_safe_call` 内置错误处理），但建议加注释说明
  - `qbit_sync.py:55` — 应通过 BGTaskManager 创建以获取关闭时的自动取消
  - `clipboard_monitor.py:67` — 同上

#### R-2: 静默异常吞没 (3 处)
- **位置**:
  - `magnet_harvester/magnet_parser.py:131` — `except Exception:` 无日志变量
  - `magnet_harvester/services/site_auth.py:31` — `except Exception:` 返回空列表
  - `magnet_harvester/qbit_client/client.py:104` — `except Exception:` 返回空 dict
- **严重度**: 🟡 MEDIUM
- **问题**: 吞没所有异常类型（包括 KeyboardInterrupt、SystemExit 等 BaseException 子类应避免捕获），且不记录异常细节，导致运行时故障难以排查
- **建议**:
  - 使用 `except Exception as e:` 并至少 `log.warning(f"... {e}")` 
  - 考虑使用 `logger.exception()` 记录完整 traceback
  - 对预期内的异常使用更具体的类型（如 `ValueError`）

#### R-3: WebSocket 死连接清理边界案例
- **位置**: `magnet_harvester/api/websocket.py:84-88`
- **严重度**: 🟢 LOW
- **问题**: 使用 `asyncio.gather(..., return_exceptions=True)` 并发发送，死连接收集到 `dead` 集合后统一移除。但 `_on_event` 通过异步回调被 MessageBus 触发，可能导致同一连接在清理中被重复尝试发送
- **建议**: 在 `_on_event` 入口处检查 `ws.client_state` 状态

---

### 安全态势评估

| 维度 | 状态 | 说明 |
|---|---|---|
| API 写保护 | ✅ 强 | 所有写端点有 `Depends(require_api_key)` |
| SSRF 防护 | ✅ 强 | url_validator 阻断 loopback/link-local/multicast/RFC1918 |
| CORS | ✅ 正确 | 默认禁用，仅在配置后启用 |
| 非 loopback 启动 | ✅ 正确 | `validate_security_posture()` 在无 API_KEY 时拒绝启动 |
| 密码存储 | ✅ 合理 | qB 密码在 .env 明文存储（此为 pydantic-settings 标准做法） |
| 默认密码 | 🟢 提示 | `admin:adminadmin` 为代码默认值，用户应在 .env 中覆盖 |
| Cookie 注入 | ✅ 安全 | 仅注入配置中显式指定的域名 |

**安全总评**: 项目安全态势良好，无不安全实践。

---

## 🔴 P0 — Critical（必须立即修复）

> ⚠️ 以下为旧报告内容，保留供历史参考。当前版本中这些问题已修复。

### 1. `InMemoryItemStore` 并发字典迭代崩溃风险

### 1. `InMemoryItemStore` 并发字典迭代崩溃风险

**位置**：`store.py:123-129`, `store.py:131-133`, `store.py:152-162`
**问题**：`list()`, `search()`, `stats()` 等方法在遍历 `self._items` 字典时，如果另一个协程同时调用 `add()` / `remove()` / `clear()`，会抛出 `RuntimeError: dictionary changed size during iteration`。
**复现场景**：WebSocket 广播器在 `_on_event` 中调用 `store.list()`，同时后台爬虫通过 `pipeline.execute()` 调用 `store.add()`。
**修复建议**：在 `InMemoryItemStore` 中添加 `asyncio.Lock`，或在遍历前复制字典视图：`list(self._items.values())` 已经是复制，但 `for item in self._items.values()` 在遍历过程中如果字典被修改仍会崩溃。应使用 `list(self._items.values())` 快照遍历（当前代码已使用，但 `stats()` 中也是 `for item in self._items.values()`，同样安全）。**实际上当前代码已使用 `list()` 包装，此问题不存在**。但 `clear()` 可能在 `list()` 调用和遍历之间执行，导致空列表。这不是崩溃，但可能丢失数据。
**重新评估**：当前 `list()` 和 `search()` 已使用 `list(self.__items.values())` 快照，不会崩溃。`stats()` 同样。但 `get_hashes_by_prefix()` 使用 `for h in self._items` 遍历键，如果同时 `clear()` 会崩溃。应改为 `list(self._items.keys())`。

### 2. `crawler.py` 共享 Set 竞态条件导致重复爬取

**位置**：`crawler.py:344-350`
**问题**：`_claim_unvisited_links` 中 `if link in visited: continue; visited.add(link)` 不是原子操作。多个 worker 协程可能在同一时刻检查到 `link not in visited`，然后都将其加入，导致同一页面被重复爬取。
**修复建议**：使用 `asyncio.Lock` 保护 `visited` 和 `seen` 集合的读写，或改用 `asyncio.Queue` 的去重机制。

### 3. `api/routes.py` SSRF 漏洞 — 任意 URL 爬取

**位置**：`api/routes.py:83-91`
**问题**：`start_crawl` 接口没有对 `req.url` 做任何验证，攻击者可以提交内网地址（如 `http://192.168.1.1:8080`、 `http://localhost:8085`、 `file:///etc/passwd`），导致服务端请求伪造（SSRF）。
**修复建议**：

1. 添加 URL 白名单 / 黑名单验证
2. 禁止内网 IP、localhost、文件协议
3. 限制只允许 http/https 协议

### 4. `api/routes.py` 关键接口无认证保护

**位置**：`api/routes.py:83-91`, `api/routes.py:94-99`, `api/routes.py:102-105`, `api/routes.py:143-160`, `api/routes.py:163-168`
**问题**：`/api/crawl`, `/api/download`, `/api/reclassify`, `/api/config` (PUT), `/api/items` (DELETE) 等接口没有任何身份验证，任何能访问服务的人都可以：

- 向 qBittorrent 添加任意磁力链接
- 修改 qBittorrent 连接配置（包括密码）
- 清空所有采集数据
- 触发任意 URL 爬取
  **修复建议**：添加 API Key / Bearer Token / Basic Auth 中间件，至少对修改类接口进行保护。

### 5. `main.py` CORS 配置过于宽松

**位置**：`main.py:47-49`
**问题**：`allow_origins=["*"]` 允许任何网站通过浏览器调用 API，配合无认证接口，攻击者可以通过构造恶意网页诱导用户触发爬取/下载操作。
**修复建议**：生产环境应设置为具体的域名白名单，或完全禁用 CORS（如果前端同域部署）。

---

## 🟠 P1 — High（严重影响功能或性能）

### 6. `qbit_sync.py` 状态同步缺少错误恢复

**位置**：`services/qbit_sync.py:85-157`
**问题**：`_run()` 循环中如果 `poll_torrent_snapshot()` 持续失败（如 qB 临时离线），会无限循环并每次打印 `log.debug`。但 `tracked_items` 列表可能包含大量 item，每次循环都全量查询 `store.list(limit=10000)` 并过滤，CPU 开销随 item 数量线性增长。
**修复建议**：

1. 添加指数退避重试，连续失败时增大轮询间隔
2. 使用 `store.get_pending()` 替代 `store.list()` + 手动过滤
3. 添加 `max_consecutive_sync_failures` 阈值，超过后暂停同步并告警

### 7. `pipeline.py` 下载阶段串行执行

**位置**：`pipeline.py:207-222`
**问题**：`_download_items` 使用 `for` 循环顺序调用 `add_magnet`，每次下载间隔 0.3 秒。如果一次爬取发现 100 个磁力链接，全部下载需要 30 秒以上。
**修复建议**：使用 `asyncio.gather()` 或 `asyncio.Semaphore` 限制并发数（如最多 5 个并发），大幅缩短批量下载时间。

### 8. `bus.py` 超时任务异常未检索导致内存泄漏

**位置**：`bus.py:63-93`
**问题**：`emit()` 中 `await asyncio.wait(tasks, timeout=1.0)` 后，pending 任务继续后台运行。如果这些任务抛出未捕获异常，Python 会记录 `Task exception was never retrieved`，且 Task 对象在事件循环中保持引用直到异常被检索，造成内存泄漏。
**修复建议**：为所有 pending 任务添加 `add_done_callback` 检索异常，或统一使用 `asyncio.gather(*tasks, return_exceptions=True)` 并设置超时。

### 9. `qbit_client/client.py` `FS_BASE_PATH` 空值导致当前目录污染

**位置**：`qbit_client/client.py:345-347`
**问题**：`fs_base = settings.FS_BASE_PATH.strip()` 如果 `.env` 中 `FS_BASE_PATH` 被注释掉或设为空字符串，`Path("") / "电影"` 会在当前工作目录创建 `电影/` 目录。如果服务在系统目录（如 `/usr/local/bin`）运行，会污染系统目录。
**修复建议**：添加非空校验：`if fs_base and fs_base.strip():` 才执行 `mkdir`。

### 10. `qbit_client/client.py` 磁力链接哈希格式校验不一致

**位置**：`qbit_client/client.py:327` vs `magnet_parser.py:23-26`
**问题**：`client.py` 使用 `r'btih:([A-Za-z0-9]{8,40})'` 匹配 base32/hex 混合字符，而 `magnet_parser.py` 使用 `r'btih:([a-fA-F0-9]{32,64})'` 严格匹配十六进制。这可能导致：

- Parser 提取失败的链接，Client 却能添加（不一致行为）
- 32 字符 base32 编码的哈希被 Client 接受，但 qBittorrent 可能拒绝
  **修复建议**：统一使用 `magnet_parser.py` 的严格十六进制正则，或统一放宽到 `[A-Za-z0-9]{32,40}` 并添加 base32→hex 转换逻辑。

### 11. `crawler.py` 详情页链接硬编码模式过于僵化

**位置**：`crawler.py:330-332`
**问题**：`_extract_detail_links` 硬编码了 `/details/`, `/detail/`, `/torrent/` 等路径模式。很多站点使用完全不同的 URL 结构（如 `/item/123`, `/post/abc`），导致深度爬取失效。
**修复建议**：

1. 支持从配置文件加载自定义模式
2. 使用启发式规则：链接文本包含 "详情"、"下载" 等关键词
3. 限制每个页面的详情链接数量（已有 50 条限制，但模式不匹配时一条都抓不到）

### 12. `config.py` 全局单例并发修改风险

**位置**：`config.py:135`
**问题**：`settings = Settings()` 是模块级全局单例。`api/routes.py:149` 中 `settings.update_qbit(...)` 直接修改全局对象。如果两个请求同时修改配置，可能产生竞态条件（如一个请求修改 host，另一个修改 password，结果配置混合）。
**修复建议**：

1. 使用 `asyncio.Lock` 保护配置修改
2. 或改用不可变配置 + 原子替换模式
3. 或禁止并发修改，返回 409 Conflict

---

## 🟡 P2 — Medium（影响体验或可维护性）

### 13. `api/routes.py` 错误事件类型滥用

**位置**：`api/routes.py:167`, `services/agent_tools.py:111`
**问题**：`clear_items` 和 `clear_all` 使用 `EventType.ERROR` 来广播 "items_cleared" 事件。语义上完全错误，会导致错误统计虚高，且前端错误处理逻辑可能误触发。
**修复建议**：添加 `EventType.ITEMS_CLEARED` 或复用 `EventType.STORE_CHANGED`。

### 14. `api/websocket.py` WebSocket 广播串行阻塞

**位置**：`api/websocket.py:71-76`
**问题**：`_on_event` 中 `await ws.send_text(data)` 是串行执行的。如果某个客户端连接很慢（如网络延迟高），会阻塞其他客户端的广播，导致实时性下降。
**修复建议**：使用 `asyncio.gather()` 并发发送，或设置发送超时。

### 15. `api/websocket.py` WebSocket 消息未处理

**位置**：`api/websocket.py:58-60`
**问题**：`handle_connection` 中 `while True: await ws.receive_text()` 只接收消息但不处理。客户端发送的任何消息（如心跳、控制命令）都被静默丢弃。
**修复建议**：解析客户端消息并支持心跳响应、订阅控制等功能。

### 16. `models.py` `MagnetItem` 缺少时间戳字段

**位置**：`models.py:22-33`
**问题**：`MagnetItem` 没有 `created_at` 或 `updated_at` 字段，无法按时间排序、无法判断 item 年龄、无法做 TTL 清理。
**修复建议**：添加 `created_at: datetime = Field(default_factory=datetime.now)` 和 `updated_at` 字段。

### 17. `store.py` `list()` 方法内存分页效率低

**位置**：`store.py:117-129`
**问题**：`list()` 先查询所有 item（`limit=10000`），排序后切片。即使只需要 20 条，也要加载和排序全部 10000 条。
**修复建议**：在 `InMemoryItemStore` 中维护一个按时间/名称排序的有序列表，或使用 `heapq.nsmallest` 做高效 Top-N 查询。

### 18. `errors.py` 错误清理逻辑计算错误

**位置**：`errors.py:111`
**问题**：`_cleanup_old_errors` 中 `to_remove = len(self._errors) - self._max_errors + 100` 当错误数为 1001 时，会移除 101 条，导致剩余 899 条（低于 max_errors 1000）。
**修复建议**：改为 `to_remove = max(0, len(self._errors) - self._max_errors)`，或只移除超出部分 + 少量缓冲。

### 19. `keyword_recognizer.py` 前缀匹配过于宽泛

**位置**：`keyword_recognizer.py:47-49`
**问题**：`n.startswith(keyword)` 会匹配任何以关键词开头的文件名。例如关键词 "AV" 会匹配 "Avatar"、"Avengers"，导致误分类。
**修复建议**：前缀匹配应要求后面紧跟分隔符（如 `.`, `_`, `-`, 空格），或仅对精确匹配使用前缀规则。

### 20. `crawler.py` 重试延迟无抖动

**位置**：`crawler.py:289`
**问题**：`_fetch_with_retry` 使用固定指数退避 `delay = 2 ** retry_count`（2, 4, 8 秒）。在并发爬取同一站点时，多个 worker 可能在同一时刻重试，形成 "惊群效应"。
**修复建议**：添加随机抖动：`delay = 2 ** retry_count + random.uniform(0, 1)`。

### 21. `qbit_client/client.py` `ensure_category` 固定 sleep 不灵活

**位置**：`qbit_client/client.py:291-314`
**问题**：创建分类后固定 `await asyncio.sleep(0.5)`，在某些慢速系统上可能不够，在快速系统上浪费时间。
**修复建议**：使用轮询验证替代固定 sleep：循环检查分类是否存在，最多等待 3 秒。

### 22. `qbit_client/client.py` 登录成功不重置失败计数

**位置**：`qbit_client/client.py:70-95`
**问题**：`_login` 成功时不重置 `consecutive_failures`，只有 `add_magnet` 成功时才重置。这意味着如果 `ping` 或 `get_maindata` 失败多次，`is_healthy()` 会持续返回 False，即使后续操作正常。
**修复建议**：在 `_login` 成功、任何 `_req` 成功返回后重置 `consecutive_failures`。

### 23. `classifier/fallback.py` 默认分类为 "电影" 过于武断

**位置**：`classifier/fallback.py:27-33`
**问题**：`classify_local` 在未匹配任何规则时默认返回 "电影"。很多内容（如普通软件、文档）会被错误分类为电影。
**修复建议**：默认返回 "其他"，让未识别内容进入人工审核队列。

### 24. `services/agent_tools.py` `reclassify_item` 路径设置错误

**位置**：`services/agent_tools.py:86`
**问题**：`store.update(match, category=cat, save_path=cat)` 将 `save_path` 直接设为分类名（如 "电影"），而不是实际文件系统路径（如 `/downloads/电影`）。这会导致 qBittorrent 分类路径错误。
**修复建议**：通过 `QBitPathResolver` 或配置映射获取分类对应的真实路径。

### 25. `services/agent_tools.py` `start_crawl` depth 无上限

**位置**：`services/agent_tools.py:63`
**问题**：Agent 工具直接调用 `pipeline.execute(url, depth=depth)`，不经过 `CrawlRequest` 的 Pydantic validator（`clamp_depth` 限制 1-3）。Agent 可能传入 `depth=10` 导致指数级页面爆炸。
**修复建议**：在 `ToolExecutor.start_crawl` 中添加 `depth = max(1, min(depth, 3))`。

---

## 🟢 P3 — Low（优化建议）

### 26. `main.py` `check_disk_space` 方法不存在

**位置**：`main.py:34`
**问题**：`settings.check_disk_space()` 被 `hasattr` 保护，但 `config.py` 中 `Settings` 类根本没有这个方法。`disk_info` 永远为 `{}`，日志中永远显示 `磁盘: ?GB`。
**修复建议**：在 `Settings` 中实现 `check_disk_space()`，或移除该日志字段。

### 27. `magnet_parser.py` `parse_magnet` 截断风险

**位置**：`magnet_parser.py:73`
**问题**：`raw.strip().rstrip("'\").split()[0]` 如果磁力链接参数值内部有空格（虽然罕见），会被截断。
**修复建议**：使用正则提取完整磁力链接，而不是 `split()[0]`。

### 28. `magnet_parser.py` Base64 解码后 UTF-8 丢失字符

**位置**：`magnet_parser.py:109`
**问题**：`decoded_bytes.decode('utf-8', errors='ignore')` 会静默丢弃非法 UTF-8 字节，可能导致解码后的磁力链接不完整。
**修复建议**：使用 `errors='replace'` 并添加日志警告，或尝试多种编码（latin-1, gbk）。

### 29. `crawler.py` `word_count_threshold=1` 过于宽松

**位置**：`crawler.py:276`
**问题**：`CrawlerRunConfig(word_count_threshold=1)` 会保留几乎所有内容，包括导航栏、页脚等无意义文本，增加解析开销。
**修复建议**：提高到 `word_count_threshold=10` 或更高，过滤掉短文本片段。

### 30. `bus.py` 无意义的 done_callback

**位置**：`bus.py:92`
**问题**：`task.add_done_callback(lambda t: None)` 没有任何作用，只是让代码阅读者困惑。
**修复建议**：移除或改为实际的异常检索回调。

### 31. `qbit_client/paths.py` 路径丢失前导斜杠

**位置**：`qbit_client/paths.py:31`
**问题**：`_extract_base_from_path` 返回的路径缺少前导 `/`（如 `"downloads"` 而非 `"/downloads"`），在路径拼接时可能产生相对路径。
**修复建议**：返回时添加前导斜杠：`return "/" + "/".join(...)` 或确保调用方处理。

### 32. `api/routes.py` `get_config` 泄露敏感信息

**位置**：`api/routes.py:135-140`
**问题**：`get_config` 返回 `qbit_username`，虽然不算高度敏感，但配合无认证接口，攻击者可以获取 qBittorrent 用户名。
**修复建议**：配置接口应要求认证，或至少不返回凭据信息。

### 33. `api/routes.py` `start_crawl` 无任务追踪

**位置**：`api/routes.py:83-91`
**问题**：后台任务创建后不返回任务 ID，前端无法查询进度、取消任务或获取结果。
**修复建议**：返回 `task_id`，并提供 `/api/tasks/{task_id}` 查询接口。

### 34. `store.py` `add_batch` 无事务语义

**位置**：`store.py:166-172`
**问题**：`add_batch` 逐条调用 `add()`，如果中途发生异常（理论上不会，因为无 await），已添加的 item 不会回滚。
**修复建议**：对于内存存储这不是大问题，但如果未来实现持久化存储，需要事务支持。

### 35. `classifier/local_classifier.py` 规则不支持热更新

**位置**：`classifier/local_classifier.py:36-39`
**问题**：`KeywordCategoryRecognizer` 在 `LocalClassifier.__init__` 中初始化，之后无法在不重启服务的情况下更新规则。
**修复建议**：添加 `/api/classifier/reload` 接口，或监听配置文件变化自动重载。

### 36. `services/qbit_sync.py` 轮询间隔不可配置

**位置**：`services/qbit_sync.py:33`
**问题**：`poll_interval=2.0` 是硬编码的。对于大型 qBittorrent 实例，2 秒可能太频繁；对于小型实例，可以更频繁。
**修复建议**：从 `settings` 读取 `QBIT_SYNC_INTERVAL`。

### 37. `pyproject.toml` 缺少 `playwright` 依赖声明

**位置**：`pyproject.toml:14`
**问题**：`crawl4ai` 内部依赖 `playwright`，但 `pyproject.toml` 没有直接声明。如果 `crawl4ai` 未来版本移除 playwright 依赖，项目会崩溃。
**修复建议**：显式添加 `playwright>=1.40` 到 dependencies。

### 38. 仓库包含不应提交的文件

**位置**：根目录
**问题**：`.DS_Store`, `node_modules/`, `package-lock.json`, `codebase_analysis.md`, `plan.md` 等文件存在于仓库中。
**修复建议**：更新 `.gitignore`，移除已提交的不必要文件。

### 39. `requirements.txt` 与 `pyproject.toml` 不同步

**位置**：`requirements.txt`
**问题**：`requirements.txt` 和 `pyproject.toml` 的依赖版本约束不一致（如 `fastapi>=0.110` vs `fastapi>=0.110.0`）。
**修复建议**：统一使用 `pyproject.toml` 作为唯一依赖源，`requirements.txt` 通过 `pip-compile` 或 `pip freeze` 生成。

### 40. 测试文件缺少集成测试

**位置**：`tests/`
**问题**：40+ 个测试文件全部是单元测试，缺少：

- 端到端 API 测试（FastAPI TestClient）
- 爬虫集成测试（mock crawl4ai）
- qBittorrent 客户端模拟测试
- 并发安全测试（多协程同时操作 store）
  **修复建议**：添加 `tests/integration/` 目录，使用 `httpx.AsyncClient` + `TestClient` 做端到端测试。

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

1. 修复 P0-3 SSRF 漏洞（URL 白名单验证）
2. 修复 P0-4 接口认证（API Key / Bearer Token）
3. 修复 P0-2 爬虫 Set 竞态条件（加锁）
4. 修复 P0-5 CORS 配置（限制域名）
5. 修复 P1-9 FS_BASE_PATH 空值保护

### 第二周（性能 + 可靠性）

6. 修复 P1-7 下载并发化（gather + Semaphore）
7. 修复 P1-6 同步循环优化（退避 + get_pending）
8. 修复 P1-8 Bus 内存泄漏（异常检索）
9. 修复 P1-10 哈希校验统一
10. 修复 P2-14 WebSocket 并发广播

### 第三周（体验 + 可维护性）

11. 修复 P2-13 错误事件类型滥用
12. 修复 P2-16 时间戳字段
13. 修复 P2-24 Agent 工具路径设置
14. 修复 P2-25 Agent depth 限制
15. 添加 P3-40 集成测试

---

_报告生成时间：2025-06-12_
_扫描工具：人工代码审查_
