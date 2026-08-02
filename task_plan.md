# 全库架构与缺陷审查计划

## 目标

基于实际业务调用链审查 Magnet Harvester，只有经独立证据二次确认的问题才进入最终报告；对低风险、范围明确的问题实施优化并验证。

## 阶段

- [x] 建立范围、读取领域词汇与 ADR、确认知识图谱可用
- [x] 第一轮：结构、核心业务流程、并发与失败路径的候选问题发现
- [x] 第二轮：调用链、可复现测试和既有测试交叉验证候选问题
- [x] 实施已确认且可安全落地的优化（C-001、C-002、C-003）
- [x] 全量回归、复审 diff、输出结论与剩余风险

## 第二轮：SQLite 异步路径

- [x] 复现并确认 SQLite 写锁会阻塞事件循环
- [x] 在 async 调用方统一隔离阻塞 adapter I/O
- [x] 回归、格式与实际 SQLite 写锁验证

## 约束

- 保留工作区既有未提交修改，不覆盖或回退它们。
- 使用 `AppContext` 集中装配的既有 ADR；不为假想变化增加 adapter/seam。
- 最终结论区分已修复、已确认未修复、以及经验证排除的候选项。

## 2026-07-17 架构复审与 TDD 优化

- [x] 读取 `CONTEXT.md`、README、ADR-0001 与 Scrapling 官方动态会话文档
- [x] 独立探索 shallow module、seam 泄漏和异步状态竞态
- [x] 输出可视化架构报告到系统临时目录
- [x] C-005：拒绝分类状态准入后，pipeline 不再调用分类 adapter
- [x] C-006：为两个 ItemStore adapter 增加原子条件更新并迁移状态转换
- [x] 全量 lint、测试与最终 diff 复审

## 2026-07-17 剩余优化闭环

### 已确认测试 seam

- `MagnetItemTransitions`：状态准入返回值及事件语义
- `ItemStore`：内存/SQLite adapter 的不可变读写契约
- `HarvestPipeline`：摄取、分类、下载的端到端行为
- `AppRuntime`：后台任务创建到关闭的生命周期
- `AppContext`：装配后的窄依赖访问

### 阶段

- [x] TDD：下载使用准入后的条目快照
- [x] TDD：手动分类不覆盖并发字段且拒绝活跃下载状态
- [x] TDD：ItemStore 不泄漏可变内部对象
- [x] TDD：统一 crawler 与 clipboard 的摄取入口
- [x] TDD：所有运行时任务统一归属 `BGTaskManager`
- [x] 收窄 `AppContext` / `QBitRuntime` 依赖并更新 ADR/领域文档
- [x] 独立 diff 复核与全量门禁
