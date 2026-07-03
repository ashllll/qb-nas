# qb-nas BUG 扫描记录

## 2026-07-03 11:22 (第二十轮 — 0 修复，10 HIGH+ 超阈值)
基线: 428 passed, 0 failed。双会话发现 1 CRITICAL + 9 HIGH + 17 MEDIUM + 15 LOW = 42 个，HIGH+ 超过 5 个阈值，**不自动修复**。

### 新发现 HIGH/CRITICAL (10 个)
🔴 CRITICAL: store.py:301 — _connect() 返回 closing(conn) wrapper，类型注解误导
🟠 HIGH: crawler.py:338 — stop() 与 crawl() 并发 self._crawler=None 导致爬取崩溃
🟠 HIGH: magnet_parser.py:75 — unquote_plus 把 + 转空格，损坏 C++/A+B 磁力链接
🟠 HIGH: transitions.py:197 — download_failed() 无状态前置检查
🟠 HIGH: routes.py:268 — update_config() 缺 RuntimeError 捕获，500 裸传播
🟠 HIGH: qbit_sync.py:123 — 每 2s 全量加载 50000 条目，终态重复扫描
🟠 HIGH: store.py:291 — _connect() 每次创建新连接，高并发 fd 耗尽
🟠 HIGH: websocket.py:42 — _active_ws set 无锁访问，快照-gather-difference_update 竞态
🟠 HIGH: store.py:596 — add_batch() commit 失败全部丢失，无部分成功
🟠 HIGH: (子会话2 第6个 HIGH 因输出截断未获取全文)

### 持续累积 (跨 R13-R20)
🔴 C1: _transport.py close()/_get_client() TOCTOU 竞态 (R13)
🔴 C2: crawler.py start() AsyncWebCrawler 泄漏态残留 (R14)
🟠 H1: websocket.py broadcast 无超时 + gather 异常静默丢弃 (R15)
🟠 H2: store.py _row_to_item 静默丢弃 → 分页空洞 (R15)
🟠 H3: pipeline.py _download_single_item wait_for 超时竞态 (R18)
🟡 C3: bus.py emit() 遍历 subscribers 无锁 → 已降为防御性 LOW (R19)
MEDIUM~LOW: routes 参数顺序不一致, websocket json.loads 无大小限制, ~15

### 趋势 (R14→R20)
- 新发现 HIGH+: 7→9→2→1→3→2→10 (R20 大幅反弹 — 可能因子会话2 审查范围更广)
- 累积未修复 CRITICAL: 3 (↑1), HIGH: 9 (↑6), MEDIUM+: ~50
- 测试始终 428 passed / 0 failed
- 累计修复: ~152 bugs (R20 未修复)

## 历史
R19: 7 fix (pipeline/qbit/transitions/bus) | R18: 10 fix (crawler/transport/routes/websocket/errors)
R17: 11 fix | R16: 7 fix | R12-R15: 49 fix | R9-R11: 21 fix | R1-R7: 48 fix
累计修复: ~152 bugs
