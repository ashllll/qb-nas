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

### 新增

- 测试：`test_studio_recognizer.py`（厂牌开放识别与误匹配防护）；`reconcile_snapshot` 的 error_msg 透传/去重/恢复清除测试；`download` 预检受理数测试；`test_qbit_save_path_concurrency.py`（路径并发探测去重/负缓存/清缓存后再探测）
