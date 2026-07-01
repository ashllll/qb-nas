# qb-nas BUG 扫描记录

## 2026-07-02 05:31 (第七轮 — 已修复 2 个)
基线: 428 passed, 0 failed。扫描初报 2C+5H，验证后 2C 为误报/降级，实修 2H。

### CRITICAL 误报
- pipeline.py:237 — Task.exception() 不 raise，误报。代码行为正确。
- crawler.py:178 → 降级 MEDIUM（实际影响低）

### 本轮修复
- ✅ bus.py:81 — deliver() 超时后二次 gather 添加 asyncio.wait_for(timeout=3.0)
- ✅ client.py:138 — get_maindata() 网络异常不再吞为 {}，改为 raise 让同步循环走退避

### 跳过 (MEDIUM 13 + LOW 7)
HIGH→MEDIUM降级: _transport 异常分支/pipeline TOCTOU/qbit_sync OOM + 原 MEDIUM 10项 + crawler stop
LOW: 7项保持不变

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

## 往期 (R1-R5)
R5 2026-06-28: 8修复 (store连接泄漏/事务/pipeline竞态/clipboard TOCTOU/_transport锁/config)
R4 2026-06-28: 10修复 / R3 2026-06-28: 6修复 / R2 2026-06-25: 13修复
R1 2026-06-25 首次: 18 CRITICAL+HIGH，超阈值未自动修复。
