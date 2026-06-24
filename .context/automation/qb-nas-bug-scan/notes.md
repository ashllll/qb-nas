# qb-nas BUG 扫描记录

## 2026-06-25 02:08

基线测试: 425 passed, 0 failed (首次运行)

### 本轮发现 (18 CRITICAL/HIGH, 未自动修复 — 超过阈值)

#### CRITICAL (4)
- [CRITICAL] store.py:93-94 — InMemoryItemStore.get() 无锁读取导致 TOCTOU 竞态。pipeline.py:144 先 get() 再 update()，中间可能被其他协程插入
- [CRITICAL] qbit_client/client.py:249-254 — OrderedDict `_category_locks` 在锁外被并发修改，可能使 OrderedDict 内部链表损坏
- [CRITICAL] api/websocket.py:39-143 — `_active_ws` 集合无锁保护，add() 与 difference_update() 并发可能导致元素丢失
- [CRITICAL] services/qbit_sync.py:104-107 — `_run()` 无锁读取 `self._qbit`/`self._store`，replace_qbit_client() 在锁内替换，可能读到被关闭的旧引用

#### HIGH (14)
- [HIGH] crawler.py:219 — `except Exception` 在 crawl() 事件循环中过于宽泛，掩盖编程错误
- [HIGH] pipeline.py:124,205 — 两个宽泛 `except Exception` 吞掉关键异常（嵌套异常处理可能丢失状态）
- [HIGH] qbit_client/client.py:123 — ping() 中 `except Exception` 宽泛，不区分网络问题和配置/认证问题
- [HIGH] store.py:311,487 — SQLite add()/add_batch() 宽泛 `except Exception` 可能吞掉数据库损坏错误
- [HIGH] services/qbit_sync.py:120 — store.list(limit=2000) 无条件全量加载，超 2000 条目会被静默截断导致 sync 遗漏
- [HIGH] api/websocket.py:127-143 — _on_event() 检查与 send_text() 之间存在 TOCTOU 窗口
- [HIGH] api/routes.py:109-120 — start_crawl() 未捕获异步异常(TransportError/CancelledError)，导致 500
- [HIGH] api/routes.py:51-59 — _task_snapshot() 用 getattr 而非 callable() 检查，可能导致 AttributeError 500
- [HIGH] api/routes.py:193-213 — update_config() 中 ValueError 异常消息可能包含密码
- [HIGH] config.py:302-308 — os.replace 跨盘符会失败(NAS 网络路径场景)
- [HIGH] services/clipboard_monitor.py:119-120 — pyperclip.paste() 异常被静默吞掉，后续监控静默失效
- [HIGH] services/clipboard_monitor.py:128-129 — _handle_item() 静默吞异常，磁力链接永久丢失不重试
- [HIGH] transitions.py:247-252 — cleared() 中 count 读取与 clear() 之间无原子性保证，返回值可能偏小
- [HIGH] qbit_client/_transport.py:64-68 — _get_client() 无锁保护，并发可能泄漏 client

#### MEDIUM (8, 未计入阈值)
- [MEDIUM] services/stats.py:30-54 — threading.Lock 在 asyncio 事件循环中阻塞
- [MEDIUM] store.py:137-138 — InMemoryItemStore.list() 不持有锁，快照可能不一致
- [MEDIUM] api/routes.py:185-188 — health_check 无认证暴露 qBittorrent 连接状态
- [MEDIUM] api/websocket.py:143-155 — 嵌套 getattr 静默失败，初始化顺序问题难诊断
- [MEDIUM] qbit_client/client.py:98-103 — _client property setter 绕过 _get_client() 生命周期管理
- [MEDIUM] services/item_queries.py:41-48 — page_items() 大 offset 时浪费内存和 I/O
- [MEDIUM] api/routes.py:80-94 — get_items() status 参数无 enum 校验
- [MEDIUM] transitions.py:83-84 — classified() 对 result 字典缺少 key 存在性检查

#### SQL 注入: ❌ 未发现 (所有查询均使用参数化 ? 占位符)
#### 路径遍历: ❌ 未发现 (paths.py 有 _safe_fs_segment 防护)
#### 认证相关: ⚠️ WebSocket /ws 无认证 (非 loopback 部署有风险)

### 状态
- 不自动修复：CRITICAL+HIGH 共 18 个，超过 5 个阈值，需人工审查后分批修复
- 下次扫描应关注：修复进展、是否引入回归
