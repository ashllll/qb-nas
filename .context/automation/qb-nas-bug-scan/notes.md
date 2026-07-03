# qb-nas BUG 扫描记录

## 2026-07-03 15:53 (第二十二轮 — 11 修复)
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
