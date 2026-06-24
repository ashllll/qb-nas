# qb-nas BUG 扫描记录

## 2026-06-25 04:19 (第二轮 — 已修复 13 个)

基线: 425 passed, 0 failed。代码已有 5 个修复提交(6bcea1b→ab7a504)，上次 18 个 BUG 中部分误报(6项确认非Bug)。重新扫描发现 5 HIGH + 8 MEDIUM，全部自动修复。

### 本轮修复 (13 个, commit: 5ea2057)
- ✅ store.py: InMemoryItemStore 5个读方法加锁 + SQLite异常类型区分
- ✅ client.py: _category_locks LRU弹出防驱逐 + _client setter改为replace_client()
- ✅ routes.py: start_crawl异常处理(503) + update_config密码脱敏 + Depends顺序/status枚举/callable检查
- ✅ transitions.py: classified()用.get() + cleared()竞态文档
- ✅ item_queries.py: offset硬上限10000
- ✅ websocket.py: 僵尸连接5分钟超时
- ✅ clipboard_monitor.py: 连续失败退避(10次→30s)
- ✅ stats.py: threading.Lock注释

### 确认误报 (6项)
- client.py:249 OrderedDict并发的asyncio操作天然原子(无await点)
- _transport.py:64 _get_client()无锁同上
- websocket.py:39 _active_ws在asyncio单线程下set操作原子
- websocket.py:127 _on_event TOCTOU有兜底
- config.py:302 os.replace同卷原子
- websocket.py:143 getattr链防御性编程

## 2026-06-25 02:08 (首次 — 未修复，超过阈值)

基线: 425 passed, 0 failed。发现 18 CRITICAL+HIGH + 8 MEDIUM，因超过 5 个 HIGH/CRITICAL 阈值未自动修复。其中 6 项后续确认为误报(asyncio单线程下原子操作)，其余在第二轮已修复。
