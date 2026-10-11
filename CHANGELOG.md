# 更新日志

本项目按「新增 / 修改 / 修复 / 移除」记录每次推送的变更。

## 未发布

### 修改

- **qB 异常状态透传真实原因**：`TorrentStatusMapper` 对 `error` / `missingFiles` / `unknown` 及无法识别的状态生成可读 `error_msg`（如 `qB 种子状态异常: missingFiles`），`reconcile_snapshot` 在同步时透传到条目并在状态恢复后自动清除；前端不再笼统显示「qB 状态暂时异常，正在重试」（同步层只报告状态，不存在自动重试），有真实原因时显示「下载失败 · 名称 · 原因」
- **厂牌识别开放化**：`StudioRule` 命中「厂牌 + YY MM DD 日期」格式但厂牌不在 `KNOWN_STUDIOS` 白名单时，不再落回「其他」，而是智能大小写后作为分类返回（排除纯数字/过短前缀的误匹配）；修复同类内容因白名单覆盖不全而一半归厂牌、一半归「其他」的不一致
- **下载提交预检与反馈**：`POST /api/download` 只受理 `pending` / `error` 状态的条目，响应新增 `skipped` 计数；全部不可提交时返回 `status: "skipped"` 且不创建后台任务；前端「已提交 N 个下载任务」改为显示服务端实际受理数与被跳过数，全部跳过时给出明确提示

### 修复

- **qB 默认路径并发探测去重**：`/api/config` 清缓存后，并发下载任务会同时进入 `get_default_save_path` 重复探测并重复打印「基础路径（从分类 …）」日志；改用 double-checked locking 串行化探测，重复网络请求与日志消除
- **下载提交静默跳过留痕**：`HarvestPipeline._download_single_item` 在条目状态不允许提交（预检后并发改动）时补 debug 日志，便于诊断被静默跳过的条目
- **qB 移除事件不再因单轮失败丢失**：`/sync/maindata` 的 `torrents_removed` 是 rid 增量，只在发生的那一轮出现、之后不会重发；原来 `QBitSyncLoop` 若在该轮 `store.list()` 抛错（或 60s 批次超时、中途停止）就丢掉本轮移除，条目永久停在「下载中」。现在循环保留本轮的移除直到对应条目真正被 `reconcile_snapshot` 处理过才消费，并与本轮在追的条目集合取交集以避免保留集无界增长
- **同步 rid 只在整份 delta 吃下后才推进**：`QBitSyncState.poll` 原来一拿到响应就推进 `_maindata_rid`，若随后解析 `torrents_removed` 抛错，qB 会认为该 delta 已送达而不再重发，那份移除连同快照变更一起丢失；改为全部解析完成后再推进

### 新增

- 测试：`test_studio_recognizer.py`（厂牌开放识别与误匹配防护）；`reconcile_snapshot` 的 error_msg 透传/去重/恢复清除测试；`download` 预检受理数测试；`test_qbit_save_path_concurrency.py`（路径并发探测去重/负缓存/清缓存后再探测）；`test_qbit_sync_loop.py` 的「移除跨轮保留」与「移除只上报一次」两条回归网；`test_qbit_sync_state.py` 的「畸形 delta 不推进 rid」测试

### 移除

- 死代码：`QBitSyncState._recently_removed` 与 `take_recently_removed()`、`QBittorrentClient.poll_torrent_snapshot()`（生产代码零调用方；保留语义已上移到 `QBitSyncLoop._pending_removed`），以及 `QBitSyncClient` Protocol 上的对应声明
