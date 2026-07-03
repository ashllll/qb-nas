# qb-nas BUG 扫描记录

## 2026-07-03 20:33 (第二十三轮 — 8 修复)
基线: 428 passed, 0 failed。双会话发现 1 CRITICAL + 6 HIGH + 10 MEDIUM + 8 LOW = 25 新问题。

### 本轮修复 (8/8 成功)
✅ crawler.py:173 — except Exception→BaseException 覆盖 CancelledError (CRITICAL)
✅ _transport.py:82 — close() 获取 _client_lock 消除 TOCTOU (HIGH, 修复 C1)
✅ pipeline.py:283 — BaseException→asyncio.CancelledError 精确匹配 (HIGH)
✅ pipeline.py:268 — dynamic_timeout min(...,300) 上限 5 分钟 (HIGH, 修复 H3)
✅ bus.py:91-111 — orphan task 日志+cancel 重试防泄漏 (HIGH)
✅ websocket.py:188 — per-client asyncio.wait_for 3s 超时 (HIGH, 修复 H1)
✅ websocket.py:186,203-204 — try/finally 确保 difference_update (MEDIUM)
✅ websocket.py:175 — _send except 扩展 CancelledError (MEDIUM)

### 持续累积
🔴 H2: store.py _row_to_item 静默丢弃 → 分页空洞 (R15, 成本高未修)
🟡 F8: clipboard_monitor _processed_content 无限增长 (R23 新发现)
🟡 F9: store.py search() 持锁 O(n) 扫描 (R23 新发现)
🟢 C2: crawler.py 泄漏态 (R22 已有效缓解，本轮修复 CancelledError 路径)
🟢 C3: bus.py emit() 订阅者遍历 (影响实际低，降级 LOW)
🟢 C1: _transport.py TOCTOU (本轮已修复 ✅)
🟢 H1: websocket.py broadcast 无超时 (本轮已修复 ✅)
🟢 H3: pipeline.py 超时无上限 (本轮已修复 ✅)

### 趋势 (R14→R23)
- 本轮修复了 3 个长期累积问题 (C1/H1/H3)，C2 也补齐了 CancelledError 路径
- 持续累积问题从 6 个降至 2 个 (H2 + F8/F9)
- 测试始终 428 passed / 0 failed
- 累计修复: ~176 bugs

## 历史
R22: 11 fix | R21: 5 fix | R20: 0 fix, 10 HIGH+ 超阈值 | R19: 7 fix | R18: 10 fix | R17: 11 fix | R16: 7 fix | R12-R15: 49 fix | R9-R11: 21 fix | R1-R7: 48 fix
基线: 428 passed, 0 failed。双会话发现 4 HIGH + 16 MEDIUM + 11 LOW = 31 新问题。

### 本轮修复 (11/11 成功)
✅ magnet_sources.py:26 — getattr 安全访问 crawl4ai 属性 (HIGH)
✅ qbit_client/client.py:301 — ensure_category() isinstance 防御 (HIGH)
✅ store.py:662 — clear() 空表 fetchone()[0]→TypeError 防护 (HIGH)
✅ services/qbit_sync.py:94 — stop() wait_for 10s 超时防护 (HIGH)
✅ pipeline.py:262 — _download_items() 空列表守卫 (MEDIUM)
✅ pipeline.py:363 — replace_download_phase() new_qbit 参数验证 (MEDIUM)
✅ _transport.py:119 — _login() 大小写不敏感比较 (MEDIUM)
✅ bus.py:104-130 — emit() asyncio.Lock 读保护 (MEDIUM, 部分缓解 C3)
✅ crawler.py:207 — crawl() asyncio.Lock + 双重检查 (MEDIUM, 部分缓解 C2)
✅ api/routes.py:279,308,321 — clear_items/clipboard 异常处理 (MEDIUM)
✅ config.py:188 — update_qbit() 回滚路径防护 (MEDIUM)
✅ store.py:643 — add_batch() commit 失败 rollback (MEDIUM)

### 持续累积
🔴 C1: _transport.py close()/_get_client() TOCTOU 竞态 (R13)
🔴 C2: crawler.py start() AsyncWebCrawler 泄漏态残留 (R14, R22 部分缓解)
🟠 H1: websocket.py broadcast+无超时+gather 异常静默丢弃 (R15)
🟠 H2: store.py _row_to_item 静默丢弃 → 分页空洞 (R15)
🟠 H3: pipeline.py _download_single_item wait_for 超时竞态 (R18)
🟡 C3: bus.py emit() 遍历 subscribers 无锁 (R19, R22 部分缓解)

### 阻塞
✅ git push 成功 (R22 恢复推送)

### 趋势 (R14→R22)
- 新发现 HIGH+: 7→9→2→1→3→2→10→5→4
- 累积未修复 CRITICAL: 2, HIGH: 3 (↓3), MEDIUM+: ~59
- 测试始终 428 passed / 0 failed
- 累计修复: ~168 bugs

## 历史
R21: 5 fix (magnet_parser/transitions/routes/store×2) | R20: 0 fix, 10 HIGH+ 超阈值
R19: 7 fix | R18: 10 fix | R17: 11 fix | R16: 7 fix | R12-R15: 49 fix | R9-R11: 21 fix | R1-R7: 48 fix
