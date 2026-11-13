# CLAUDE.md

本仓库的代理说明以 [AGENTS.md](AGENTS.md) 为准，先读它。

## 导航

- 架构、模块、项目结构：[docs/agents/architecture.md](docs/agents/architecture.md)
- 开发与缺陷流程：[docs/agents/development-workflow.md](docs/agents/development-workflow.md)
- GitHub/gh 约束：[docs/agents/github-workflow.md](docs/agents/github-workflow.md)
- Issue、标签、领域文档：[issue-tracker.md](docs/agents/issue-tracker.md)、[triage-labels.md](docs/agents/triage-labels.md)、[domain.md](docs/agents/domain.md)

## 不可违反

- Python 3.11（`.python-version`）；不要用 3.15 跑测试。
- 事件 `updated_at` 用 UTC-aware ISO；不要混入 naive/local 时间。
- 状态变更走 transitions + `MessageBus`；不要直接写 WebSocket。
- 后台任务用 `BGTaskManager`，不用裸 `asyncio.create_task`。
- 提交、推送、关闭 issue 按当前任务授权执行；未授权先问。
