# qb-nas BUG 扫描记录

## 2026-07-02 14:36 (第十一轮 — 已修复 9 个)
基线: 428 passed, 0 failed。直接修复上轮遗留的 5 HIGH + 2 MEDIUM + 本轮 2 MEDIUM = 9 项。

### 本轮修复
- ✅ routes.py:184 — reclassify 添加 try/except 异常处理（对齐 start_crawl/download_selected）
- ✅ websocket.py:133 — _send_control 异常时调用 self.remove(ws) 清理僵尸连接
- ✅ websocket.py:70 — send_init_from_store 包裹 try/except + 日志
- ✅ observability.py:82 — get_stats() 加 asyncio.wait_for(timeout=5.0)
- ✅ clipboard_monitor.py:211 — _handle_item 双空分支后加 else: log.warning
- ✅ models.py:41 — progress 验证器加 NaN 检查 (v != v)
- ✅ websocket.py:89 — receive_text 日志移除冗余 exc_info=True
- ✅ rule.py:95 — FallbackRule 添加 reload() 方法，支持热更新
- ✅ _transport.py:181 — 不可重试 HTTP 状态码(400/404)立即 break 不再浪费重试
- 测试适配: test_local_classifier.py 断言 rules_reloaded: 1→2
- 测试适配: test_observability.py FakeQbit.get_stats() → async def

### 上轮新发现 → 本轮已修复
HIGH 5 项全部修复 / MEDIUM 4 项修复（上轮 3 + 本轮 1）

### 已知未修复
CRITICAL 3: crawler 启动竞态 / pipeline 事件绕行 / sync_state poll 竞态
HIGH 4: site_auth Cookie 解析 / qbit_sync 全量加载 / pipeline 重复分类 CAS 增强 /
        qbit_client 缓存TTL/假阴性/前缀碰撞
MEDIUM ~12 / LOW ~13

## 2026-07-02 12:22 (第十轮 — 未自动修复)

## 2026-07-02 10:07 (第九轮 — 已修复 7 个)
基线: 428 passed, 0 failed。排除已知问题后扫描发现 4H+5M+8L=17 新项。修复全部 4 HIGH + 前轮 3 个简单项(共 7 修复)。

### 本轮修复
- ✅ user_actions.py:57 — record_download() 移到 _spawn() 成功后，统计不再虚高
- ✅ routes.py:165 — download_selected 添加 try/except 包裹（对齐 start_crawl）
- ✅ store.py:354 — 移除 INSERT OR IGNORE 后的死代码 except IntegrityError
- ✅ crawler.py:306 — 空字符串 hash 绕过检测：`is None` → `not hash_key`
- ✅ observability.py:67,83 — ping() 加 asyncio.wait_for(timeout=5.0) 防端点挂起
- ✅ transitions.py:137 — manually_classified 保留原有 save_path 不再强制清空
- ✅ transitions.py:101 — started() 加 classifying 状态检查防并发重复分类
- 测试适配: test_agent_tool_path.py 断言更新为保留 save_path

### 已知未修复 (仍超阈值遗留)
CRITICAL 3: crawler 启动竞态 / pipeline 事件绕行 / sync_state poll 竞态
HIGH 6: site_auth Cookie 解析 / qbit_sync 全量加载 / pipeline 重复分类 CAS 增强 /
        qbit_client 缓存TTL/假阴性/前缀碰撞 / 剪贴板 fallback 不一致(新)
MEDIUM: ~16 项 / LOW: ~13 项

### 新发现(本轮,未修)
HIGH 0 (已全部修复) / MEDIUM 5: crawler 异常退出时资源残留 / classifier 回调异常 /
  FallbackRule 不支持 reload / WebSocket 错误路径静默 / ping 超时已修
LOW 8: 略

### 误报
- bus.py:108 setdefault → Python dict 方法名正确，非 bug

## 往期 (R1-R8)
R8: 未修复(超阈值) → R7:2修复 → R6:9修复 → R5:8修复 → R4:10修复 → R3:6修复 → R2:13修复 → R1首次18C+H超阈值
