# 生命周期与契约加固 (Hardening: Lifecycle & Contract) — 2026-08-05

**Goal:** 修复四组已确认问题中的 C+D 批次：优雅关闭生命周期、事件类型契约、Bus 超时文档一致性、WebSocket API Key 日志脱敏。A+B 批次（Fake-IP 安全评估、爬虫性能基准）待真实环境验证后另行规划。

**Status:** Approved (user) — 2026-08-05，三个决策点已确认。

---

## 1. Background

基于第二轮只读侦察（commit `f162bde` 基线，514 tests 全绿），确认以下问题：

| #   | 问题                                                                                                                                                 | 证据位置                                                                                      |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| C   | `AppRuntime.stop()` 未显式调用 `ClipboardMonitor.shutdown()` / `WSBroadcaster.shutdown()`，clipboard 残留 `_running=True`，broadcaster 残留 bus 订阅 | `assembly.py:58-76`, `services/clipboard_monitor.py:125-128`, `api/websocket.py:41,50-55`     |
| D1  | `Event.as_dict()` 允许 data 的 `"type"` 键覆盖事件类型；现存冗余违规点                                                                               | `bus.py:44-45`, `transitions.py:104`                                                          |
| D2  | MessageBus emit docstring 写"1 秒超时"，实现为 5 秒                                                                                                  | `bus.py:54,145`, `test_bus_memory_leak.py:71`                                                 |
| D3  | WS API Key 走 query param，uvicorn access log 默认记录完整 request line（含明文 key）                                                                | `run.py:12-17`, `static/app.js:118`, [uvicorn Logging](https://uvicorn.dev/concepts/logging/) |

## 2. Decisions

1. **C — 最小 seam 优雅关闭**：`AppRuntime.stop()` 在 `bg_manager.shutdown()` **之前**插入 `await clipboard_monitor.shutdown()`（内部已判 `is_running`），之后插入 `broadcaster.shutdown()`（退订 MessageBus）；同步更新 `test_main_assembly.py` 关闭顺序断言。**不**向活跃 WS 连接发 close frame（更大 seam，本轮不做）。
2. **D1 — 全局剥离**：`Event.as_dict()` 中剥离 data 的 `"type"` 键（`{"type": self.type.value, **{k: v for k, v in self.data.items() if k != "type"}}`），一处修复覆盖所有发射点；删除 `transitions.py:104` 冗余 `"type"` 键；上一轮 pipeline 剥离补丁保留（防御性，无害）。
3. **D2 — 文档对齐实现**：`bus.py` emit docstring 与 `test_bus_memory_leak.py:71` 注释统一为 5 秒；不改实现（改实现需重调 3 个测试计时断言，无收益）。
4. **D3 — access log 脱敏**：保持 query param 认证不变，`run.py` 配置 uvicorn 自定义 access formatter，将 request line 中 `api_key=<value>` 替换为 `api_key=***`；不迁移 `Sec-WebSocket-Protocol`（本轮不做）。

## 3. Scope

**In scope:**

- `assembly.py` stop() 关闭顺序 + clipboard/broadcaster 清理
- `bus.py` as_dict() 类型剥离 + docstring 修正
- `transitions.py:104` 冗余键删除
- `run.py` uvicorn access formatter 脱敏
- 对应测试更新与新增
- `doc/specs/2026-08-05-hardening-lifecycle-contract.md`（本 spec）

**Out of scope (A+B 批次，待环境验证):**

- `CRAWLER_ALLOW_FAKE_IP` 豁免策略调整（需 NAS 网络/代理链路验证）
- 爬虫解析 to_thread/阈值化（需真实页面基准）
- 活跃 WS 连接优雅关闭（close frame）
- WS 认证迁移 `Sec-WebSocket-Protocol`
- MessageBus 超时实现值调整（维持 5s）

## 4. Acceptance Criteria

1. `AppRuntime.stop()` 关闭顺序为 `sync_loop → clipboard → bg_tasks → broadcaster → crawler → qbit`；clipboard `_running` 在 stop 后被清理；broadcaster 退订后不再接收事件。
2. `Event(EventType.X, {"type": "other", ...}).as_dict()["type"] == "x"` 恒成立（data 中任何 `type` 键被剥离）；`transitions.py:104` 无冗余 `"type"` 键。
3. `bus.py` docstring 与 `test_bus_memory_leak.py:71` 注释均写 5 秒；无 "1 秒超时" 残留。
4. uvicorn access log 中 `/ws?api_key=<secret>` 显示为 `api_key=***`；单元测试验证 formatter 输出不含明文 key。
5. 全量门禁通过：pytest、ruff、`git diff --check`；无回归。

## 5. Testing Approach

- 每个修复一条 TDD 垂直切片（RED → GREEN）。
- 修改/新增测试文件：
  - `tests/test_main_assembly.py` — 关闭顺序断言更新 + clipboard/broadcaster 清理存在性
  - `tests/test_error_event_type.py` — as_dict 剥离 data type 键通用断言（`test_bus.py` 不存在，归入既有事件类型契约测试）
  - `tests/test_item_events.py` — ITEMS_CLEARED 事件无冗余 type 键（ITEMS_CLEARED 断言现有归属；`test_transitions_events.py` 无相关用例）
  - `tests/test_logger.py` — formatter 脱敏断言（文件已存在，不新增测试文件；formatter 定义为 `run.py` 模块级函数，`if __name__ == "__main__"` 保护下可安全 `import run` 直接断言）
  - `tests/test_bus_backpressure.py` / `test_bus_memory_leak.py` — 注释同步（无断言变更）
- 全量门禁：`.venv/bin/python -m pytest tests -q`、`.venv/bin/ruff check magnet_harvester tests`、`git diff --check`。

## 6. Documentation impact

- Feature / user-facing docs introduced: none
- Materially amended existing docs: none（`run.py` 日志行为变化属实现内部，README 未承诺 access log 格式）
- Derived / memory docs invalidated: none（AGENTS.md 未涉及 access log 格式或关闭顺序细节）

## 7. Open Questions

- 无（三个决策点已批准）。
