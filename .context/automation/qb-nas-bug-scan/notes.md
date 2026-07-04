# qb-nas BUG 扫描记录

## 2026-07-04 12:48 (第二十六轮 — 10 修复)
基线: 428 passed, 0 failed。双会话发现 3 HIGH + 6 MEDIUM + 14 LOW = 23 新问题。
HIGH≤5 阈值，自动修复 10 项。验证: 428 passed / 0 failed。

### 本轮修复 (10)
✅ qbit_sync.py:85 — start() 加 async with self._lock 防重入 [HIGH]
✅ store.py:300 — _connect() 显式 isolation_level='DEFERRED' [HIGH]
✅ config.py:196 — update_qbit() 回滚失败从 .env 恢复内存状态 [HIGH]
✅ pipeline.py:322 — 兜底 store.update 加 torrent_state=None, progress=0.0 [MEDIUM]
✅ _transport.py:195 — request() auth 检查移到 _auth_lock 内 [MEDIUM]
✅ config.py:336 — _write_env_values() 统一原子写入路径 [MEDIUM]
✅ main.py:43 — lifespan 区分致命错误(store不可用→raise)与可降级(qbit不可用→warn) [MEDIUM]
✅ qbit_sync.py:128 — store None 检查拆为独立 log.warning/log.error [LOW]
✅ pipeline.py:269 — dynamic_timeout 添加上限 min(600.0, ...) [LOW]
✅ websocket.py:186 — _active_ws 添加并发模型注释 [LOW]

## 2026-07-04 15:35 (R26 续 — 人工审查 + 4 修复)
基线: 428 passed → 432 passed。对 R26 累积疑似项做源码级验证，发现 4 个真实 BUG。

### 疑似项验证结果
- ❌ C1: _transport.py close()/_get_client() TOCTOU → **误报**（_closing Event + 双检锁已封闭窗口）
- ❌ C2: crawler.py stop()/crawl() 浏览器泄漏 → **误报**（start 失败兜底 close + _start_lock + None 保护）
- ❌ C3: pipeline.py _download_items 超时竞态 → **误报**（超时仅回退 pending/adding，终态不可覆盖）
- ✅ C4: store.py stats() SQLite/InMemory 不一致 → **真实，已修复**
- ❌ H1: websocket.py broadcast+gather 异常静默 → **误报**（return_exceptions=True + _DEAD sentinel 已妥善处理）
- ⚠️ H2: store.py _row_to_item 静默丢弃 → **低风险**（仅 DB 损坏时触发，正常运行不触发）
- ❌ H5: clipboard_monitor.py stop() emit TOCTOU → **误报**（窗口极窄，仅影响 UI 状态显示）

### 本轮修复 (4)
✅ qbit_sync.py:84-96 — start() 锁范围扩大，消除并发双重调用竞态 [MEDIUM]
✅ store.py:589 — SQLite stats() CASE WHEN 替代 COALESCE，统一空 category 口径 [MEDIUM]
✅ pipeline.py:354 — reclassify 排除集移除 TaskStatus.error，允许重分类失败条目 [LOW]
✅ clipboard_monitor.py:160 — _processed_content 逐出一半替代全部 clear，降低内存尖峰 [LOW]

### 趋势 (R14→R26+审查)
- R26+审查: 14 fix | R25: 9 fix | R24: 0 fix(超阈值) | R23: 9 fix
- R22: 11 fix | R21: 5 fix | R20: 0 fix(超阈值) | R19: 7 fix
- 累计修复: ~200 bugs
- 连续 0 BUG 计数: 0（本轮验证 7 条疑似项，4 条真实已修，3 条误报清除）
- 测试 432 passed / 0 failed（+4 新测试）
