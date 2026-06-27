# qb-nas BUG 扫描记录

## 2026-06-28 07:05 (第四轮 — 已修复 10 个)

基线: 428 passed, 0 failed。并行扫描发现 3 HIGH + 6 MEDIUM + 1 extra MEDIUM(stop()同理)。全部自动修复。

### 本轮修复 (10 个)
- ✅ pipeline.py: `_download_items` TimeoutError 只回退 pending/adding 条目，不覆盖已成功提交的
- ✅ pipeline.py: `_download_single_item` 空 category 记录 warning 并调 download_failed 回退
- ✅ pipeline.py: `_stream_classify` 只回退未完成 on_result 回调的条目，避免覆盖已完成条目 error_msg
- ✅ pipeline.py: `reclassify` 排除 classifying 状态，避免并发重复分类
- ✅ store.py: `_row_to_item` 反序列化失败时 log.error 记录 hash 和异常（不再静默丢弃）
- ✅ routes.py: `update_config` ValueError handler 传递 str(exc) 给客户端
- ✅ routes.py: `get_items` 添加 category 参数校验（VALID_CATEGORIES 常量）
- ✅ _transport.py: `_record_success()` 收紧为 `status_code == 200`
- ✅ config.py: `_write_env_values` write_text 移入 try 块防孤儿 tmp
- ✅ clipboard_monitor.py: `start()` 和 `stop()` 中 _bus.emit 移入锁内

### 本轮跳过
- LOW: 4 项（transitions download_failed 未清 torrent_state、sync_state 浅拷贝、transport 宽泛 Exception、config 重复键）

### 已知持续跳过
- MEDIUM: routes.py start_crawl 错误码混用（需人工判断）
- MEDIUM: app_context.py replace_qbit_config 状态不一致（需架构讨论）

## 2026-06-28 04:51 (第三轮 — 已修复 6 个)
基线: 428 passed。修复 2 HIGH + 4 MEDIUM（pipeline 异常回退、crawler hash 保护、transport 竞态、assembly stop 隔离、clipboard_monitor stop 超时）。

## 2026-06-25 04:19 (第二轮 — 已修复 13 个)
基线: 425 passed。修复 5 HIGH + 8 MEDIUM（store 加锁、client LRU/setter、routes 异常/脱敏、transitions 竞态、websocket 僵尸连接等）。

## 2026-06-25 02:08 (首次 — 未修复，超过阈值)
基线: 425 passed。发现 18 CRITICAL+HIGH，因超阈值未自动修复。6 项后续确认误报。
