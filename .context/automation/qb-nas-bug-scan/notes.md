# qb-nas BUG 扫描记录

## 2026-07-03 02:09 (第十六轮 — 7 修复)
基线: 428 passed, 0 failed。双会话发现 2 HIGH + 11 MEDIUM 新问题，<5 阈值，自动修复 7 个。

### 本轮修复 (7/7 成功)
✅ pipeline.py:269 — 非 CancelledError 异常也调用 download_failed (HIGH)
✅ store.py:275 — _connect() 返回 closing(conn) 防连接泄漏 (HIGH)
✅ transitions.py:194-198 — failed() 读取真实 previous_status (MEDIUM)
✅ _transport.py:123-126 — _login() re-raise RuntimeError (MEDIUM)
✅ user_actions.py:37 — _spawn() catch RuntimeError (MEDIUM)
✅ api/routes.py:179-182 — download_selected isinstance 校验 (MEDIUM)
✅ config.py:312-321 — .env 写入空文件防护 (MEDIUM)

### 持续累积的已知问题 (跨 R13-R16)
🔴 C1: _transport.py close()/_get_client() TOCTOU 竞态 (R13)
🔴 C2: crawler.py start() AsyncWebCrawler 泄漏态残留 (R14)
🔴 C3: bus.py emit() 遍历 subscribers 无锁 (R15)
🟠 H1: transitions.py reconcile_snapshot was_removed 未排除 error (R15)
🟠 H2: pipeline.py classification_started 失败后条目仍在流程 (R14)
🟠 H3: websocket.py broadcast 无超时 + gather 异常静默丢弃 (R15)
🟠 H4: store.py _row_to_item 静默丢弃 → 分页空洞
🟠 H5: user_actions.py download() 不返回 task_id
🟠 H6: errors.py record() details dict 并发变异 (R15)
🟠 H7: _transport.py request() 底部死代码 (R15)
🟠 H8: api/routes.py start_crawl 返回值未校验类型 (R15)
MEDIUM: store.py add_batch SAVEPOINT/宽except, item_queries 分页内存, pipeline 超时竞态
LOW~25

### 已修复的历史问题
R16: 4 已知 fix (qbit_sync 单条隔离/transitions submitting 检查 + 本轮 7 新)
R12-R15: 21 修复 | R9-R11: 21 修复 | R1-R7: 48 修复

## 趋势 (R13→R16)
- 新发现 HIGH+ 速率: 22→7→9→2 (显著下降)
- 累积未修复 CRITICAL: 3, HIGH: 8
- 测试始终 428 passed / 0 failed
- 本轮首次修复 store.py 连接泄漏和 pipeline.py 异常处理路径
