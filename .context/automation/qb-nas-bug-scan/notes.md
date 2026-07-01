# qb-nas BUG 扫描记录

## 2026-07-02 03:12 (第六轮 — 已修复 9 个)
基线: 428 passed, 0 failed。1 HIGH + 9 MEDIUM，全部修复（跳过7 LOW）。

### 本轮修复
- ✅ qbit_sync.py: classifying条目竞态错误 → 移除classifying + _stop_event守卫
- ✅ crawler.py: 单页异常终止session → per-page try/except
- ✅ pipeline.py: download_failed卡adding → 兜底store.update
- ✅ transitions.py: _emit_download_result TOCTOU → emit前重新get
- ✅ store.py: get_hashes_by_prefix缺ESCAPE → 添加ESCAPE子句
- ✅ user_actions.py: download()虚假started → _spawn返回bool
- ✅ bg_tasks.py: shutdown二轮gather无超时 → wait_for 5s
- ✅ websocket.py: 序列化失败静默丢广播 → 字段安全降级

### 跳过
- LOW: 7项 / MEDIUM(item_queries TOCTOU): 1项

## 2026-06-28 09:20 (第五轮 — 已修复 8 个)
基线: 428 passed, 0 failed。并行扫描发现 5 HIGH + 3 MEDIUM。全部自动修复。

### 本轮修复 (8 个)
- ✅ store.py: `_connect()` 改为 @contextmanager，finally 中 close() 防止连接泄漏（影响 13 处调用）
- ✅ store.py: `add_batch` 添加 BEGIN IMMEDIATE / COMMIT / ROLLBACK 显式事务
- ✅ pipeline.py: `_spawn()` except AttributeError 改为 log.warning 不再静默吞异常
- ✅ pipeline.py: `start_crawl()` task_id 改为 getattr + UUID 兜底
- ✅ pipeline.py: `_stream_classify` result_events list→dict[int, Task] 修复索引错位
- ✅ clipboard_monitor.py: `_handle_item()` 两处 await 前加 _running 检查防 TOCTOU
- ✅ _transport.py: `_get_client()` 添加 asyncio.Lock 双重检查锁
- ✅ _transport.py: `request()` raise None → 显式 None 检查
- ✅ config.py: `_env_line_key` 移除 key[0].isdigit() 限制
- ✅ tests: 更新 3 个 clipboard_monitor 测试适配 _running 守卫

### 本轮跳过
- LOW: 11 项（性能/风格/语义微优化）
- MEDIUM: LRU 驱逐拒绝服务、get_default_save_path 缓存无锁、/api/config 认证、WebSocket 丢连接、下载超时竞态等（复杂度较高或需架构讨论）

### 已知持续跳过
- MEDIUM: routes.py start_crawl 错误码混用（需人工判断）
- MEDIUM: app_context.py replace_qbit_config 状态不一致（需架构讨论）

## 往期 (R2-R4)
R2 2026-06-25: 13修复 / R3 2026-06-28: 6修复 / R4 2026-06-28: 10修复
R1 2026-06-25 首次: 18 CRITICAL+HIGH，超阈值未自动修复。
