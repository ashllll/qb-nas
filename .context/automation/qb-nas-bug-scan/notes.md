# qb-nas BUG 扫描记录

## 2026-07-03 06:46 (第十八轮 — 10 修复)
基线: 428 passed, 0 failed。双会话发现 3 HIGH + 7 MEDIUM，<5 HIGH，自动修复 10 个。

### 本轮修复 (10/10 成功)
✅ crawler.py:169 — start() 部分初始化 → 局部变量先完成再赋值 (HIGH)
✅ _transport.py:99 — _login() 缺重试 → 指数退避 retry (HIGH)
✅ _transport.py:47 — _authenticated 并发 → asyncio.Lock 保护 (MEDIUM)
✅ routes.py:188 — reclassify 缺 error→503 检查 (HIGH)
✅ pipeline.py:307 — 兜底路径缺 STORE_CHANGED → 补充事件 (MEDIUM)
✅ websocket.py:158 — 序列化失败事件丢失 → 最终降级消息 (MEDIUM)
✅ transitions.py:187 — submitted() 硬编码 previous_status → 先 get 再传 (MEDIUM)
✅ clipboard_monitor.py:206 — 自动下载丢弃返回值 → 检查 status (MEDIUM)
✅ errors.py:67 — record() details 合并 → error_id 纳入关键 details (MEDIUM)
✅ user_actions.py:88 — 前缀多匹配静默 → 返回 ambiguous (MEDIUM)

### 持续累积 (跨 R13-R18)
🔴 C1: _transport.py close()/_get_client() TOCTOU 竞态 (R13)
🔴 C2: crawler.py start() AsyncWebCrawler 泄漏态残留 (R14)
🔴 C3: bus.py emit() 遍历 subscribers 无锁 (R15)
🟠 H1: websocket.py broadcast 无超时 + gather 异常静默丢弃 (R15)
🟠 H2: store.py _row_to_item 静默丢弃 → 分页空洞 (R15)
🟠 H3: pipeline.py _download_single_item wait_for 超时竞态 (R18 新)
MEDIUM~LOW: api routes 类型校验, item_queries 分页内存, ~20

### 趋势 (R14→R18)
- 新发现 HIGH+: 7→9→2→1→3 (R18 反弹)
- 累积未修复 CRITICAL: 3, HIGH: 3, MEDIUM+: ~20
- 测试始终 428 passed / 0 failed
- 累计修复: ~145 bugs

## 历史
R17: 11 fix | R16: 7 fix | R12-R15: 49 fix | R9-R11: 21 fix | R1-R7: 48 fix
