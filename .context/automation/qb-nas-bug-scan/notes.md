# qb-nas BUG 扫描记录

## 2026-07-04 10:28 (第二十五轮 — 9 修复)
基线: 428 passed, 0 failed。双会话发现 4 HIGH + 12 MEDIUM + 8 LOW = 24 新问题（排除已知10项）。
HIGH≤5 阈值，自动修复 9 项。验证: 428 passed / 0 failed。

### 本轮修复 (9)
✅ errors.py: clear_resolved() → clear_all() + mark_resolved() [HIGH]
✅ crawler.py:171-176 — start() 异常吞没, close 异常链到原异常 [HIGH]
✅ qbit_sync.py:85 — start() 加 _stop_event.clear() 修复重启失效 [MEDIUM]
✅ clipboard_monitor.py:66,160 — _processed_content 上限 10000 防泄漏 [MEDIUM]
✅ clipboard_monitor.py:61,169 — 删除 _last_seen 死代码 [LOW]
✅ transitions.py:273-277 — reconcile_snapshot map() 包 try/except [MEDIUM]
✅ transitions.py:85-90 — found() emit 失败记录 warning 不丢数据 [MEDIUM]
✅ pipeline.py:348 — reclassify hashes 去重 [MEDIUM]
✅ routes.py:207 — reclassify 错误消息空字符串 fallback [LOW]

### 持续累积（仍未修复）
🔴 C1: _transport.py close()/_get_client() TOCTOU (R13, 高复杂度)
🔴 C2: crawler.py stop()/crawl() 浏览器进程泄漏竞态 (R14/R22/R24, 高复杂度)
🔴 C3: pipeline.py _download_items 超时竞态 → 虚假 error (R18/R24, 高复杂度)
🔴 C4: store.py stats() SQLite/InMemory 不一致 (R24, 中复杂度)
🟠 H1: websocket.py broadcast+gather 异常静默 (R15/R23/R24, 中复杂度)
🟠 H2: store.py _row_to_item 静默丢弃 → 分页空洞 (R15, 中复杂度)
🟠 H5: clipboard_monitor.py stop() emit TOCTOU (R24, 中复杂度)

### 本轮未修复（非阻塞，低优先级/高复杂度跳过）
3 HIGH: pipeline.py 分类下载竞态, qbit_client 返回值语义不一致,
  errors.py 已修复 → 剩余 2 HIGH 移入下次评估

### 趋势 (R14→R25)
- R25: 9 fix | R24: 0 fix(超阈值) | R23: 9 fix | R22: 11 fix
- R21: 5 fix | R20: 0 fix(超阈值) | R19: 7 fix | R18: 10 fix
- 累计修复: ~186 bugs
- 连续 0 BUG 计数: 0（本轮发现 24 个）
- 测试始终 428 passed / 0 failed
