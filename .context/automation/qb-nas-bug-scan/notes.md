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

### 持续累积（仍未修复）
🔴 C1: _transport.py close()/_get_client() TOCTOU (R13, 高复杂度)
🔴 C2: crawler.py stop()/crawl() 浏览器进程泄漏竞态 (R14/R22/R24, 高复杂度)
🔴 C3: pipeline.py _download_items 超时竞态 → 虚假 error (R18/R24, 高复杂度)
🔴 C4: store.py stats() SQLite/InMemory 不一致 (R24, 中复杂度)
🟠 H1: websocket.py broadcast+gather 异常静默 (R15/R23/R24, 中复杂度)
🟠 H2: store.py _row_to_item 静默丢弃 → 分页空洞 (R15, 中复杂度)
🟠 H5: clipboard_monitor.py stop() emit TOCTOU (R24, 中复杂度)

### 趋势 (R14→R26)
- R26: 10 fix | R25: 9 fix | R24: 0 fix(超阈值) | R23: 9 fix
- R22: 11 fix | R21: 5 fix | R20: 0 fix(超阈值) | R19: 7 fix
- 累计修复: ~196 bugs
- 连续 0 BUG 计数: 0（本轮发现 23 个）
- 测试始终 428 passed / 0 failed
