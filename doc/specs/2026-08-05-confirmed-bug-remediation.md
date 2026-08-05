# 已确认缺陷修复 (Confirmed Bug Remediation) — 2026-08-05

**Goal:** 修复全面审查中已确认的 6 个缺陷，全部采用最小改动，不改变现有架构边界与事件语义。

**Status:** Approved (user) — 修复范围与设计决策已于 2026-08-05 批准。

---

## 1. Background

基于只读子 Agent 全面审查（后端 / 前端 / 架构安全三路）与主 Agent 代码复核，确认以下问题：

| #   | 问题                                                                                      | 证据位置                                                                     |
| --- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 1   | 前端无 WS 心跳，5 分钟空闲被服务端断开，重连 `itemState.reset()` 清空用户选择             | `static/app.js:111-131`, `magnet_harvester/api/websocket.py:143-153`         |
| 2   | `crawl_error` 事件被 data 内 `type` 键覆盖为 `error`，前端无分支，失败时 UI 永久"爬取中"  | `magnet_harvester/bus.py:45`, `pipeline.py:153-160`, `static/app.js:142-245` |
| 3   | REST API Key 非 ASCII → `secrets.compare_digest` TypeError → 500（WS 路径已正确返回 401） | `magnet_harvester/utils/auth.py:25`                                          |
| 4   | `DownloadRequest.hashes` 无长度上限，一次性创建大量协程                                   | `magnet_harvester/models.py:72-74`, `pipeline.py:269-280,320-322`            |
| 5   | `AUTO_CREATE_DIRS` 死配置：目录创建只判断 `FS_BASE_PATH`，开关无效                        | `config.py:109`, `qbit_client/submitter.py:122-124`                          |
| 6   | `replace_client()` 死代码，docstring 承诺"惰性重建"与 `_closing` 永不复位的实现矛盾       | `qbit_client/client.py:72-74`, `qbit_client/_transport.py:80-91`             |

## 2. Decisions

1. **WS 心跳**：前端在连接建立后每 30 秒发送文本 `"ping"`（服务端 `handle_client_message` 已支持），`onclose` 时清理定时器。固定 30s，不做指数退避（重连保持 2s）。
2. **crawl_error 修复分层**：
   - **发射层**（`pipeline.py:160`）：`Event(EventType.CRAWL_ERROR, msg)` 改为剥离爬虫消息中的 `"type"` 键（`{k: v for k, v in msg.items() if k != "type"}`），保持 `Event.as_dict()` 全局语义不变。
   - **前端**：`handleMsg` 新增 `case "crawl_error"`：`setCrawling(false)` + `addLog(msg.msg || msg.message || "爬取失败")` + toast；同时 `case "error"` 也复位爬取状态（防 qbit_sync ERROR 等场景遗留卡死），并统一读取 `msg.msg || msg.message`。
   - 不修改 `Event.as_dict()` 本身（涉及所有发射点，面大风险高）。
3. **非 ASCII API Key**：`require_api_key` 包一层 `try/except TypeError → 401`，与 WS 路径对称；不做编码归一化。
4. **hashes 上限**：`DownloadRequest.hashes: List[str] = Field(min_length=1, max_length=500)`；空列表 422，超限 422（FastAPI/Pydantic 自动）。不改 hash 格式校验（不存在 hash 仍由 pipeline 静默跳过）。
5. **AUTO_CREATE_DIRS 总开关**：`submitter.py:122` 条件改为 `if self._fs_base_path and self._auto_create_dirs:`；`MagnetSubmitter` 构造时接收该配置（从 `QBitConfig` 传入）。`.env.example` 注释更新为"FS_BASE_PATH 非空时是否自动创建分类目录"。
6. **删除 `replace_client()`**：`qbit_client/client.py:72-74` 整个方法删除；`QBitRuntime` 热替换路径（`app_context.py`）不依赖它，不受影响。同步删除/更新引用该方法的测试（若有）。

## 3. Scope

**In scope:**

- 上述 6 项修复 + 各自回归测试（全部落在既有测试文件，无新增测试文件）
- `.env.example` 中 `AUTO_CREATE_DIRS` 注释
- 前端 `app.js` 对应 handler 修改

**Out of scope (后续项，不在此轮):**

- `CRAWLER_ALLOW_FAKE_IP=true` 生产配置评估（需网络/代理信息）
- 爬虫 CPU 密集解析性能优化（需真实页面基准）
- `AppRuntime.stop()` 生命周期补全（需关闭压力测试）
- CSP 响应头与 WS 日志脱敏
- `Event.as_dict()` 全局语义重构
- hash 格式 40 位 hex 校验

## 4. Acceptance Criteria

1. 前端连接后每 30s 发送 ping；integration 测试断言服务端收到 ping 并返回 pong；连接关闭时定时器清理。
2. 后端 `crawl_error` 事件 payload 的 `type` 恒为 `"crawl_error"`（单元测试断言 `as_dict()`）；前端收到 `crawl_error` 后按钮复位并显示错误日志；`error` 分支字段兼容 `msg`/`message`。
3. 非 ASCII API Key（配置或请求头）→ REST 返回 401 而非 500；ASCII 正常路径不受影响。
4. `POST /api/download` / `POST /api/reclassify` 空列表 → 422；>500 → 422；1–500 正常。
5. `AUTO_CREATE_DIRS=false` 且 `FS_BASE_PATH` 非空 → 不创建目录；`=true` 时行为与现状一致（创建）。
6. `replace_client` 从源码删除；`grep -rn "replace_client" magnet_harvester tests` 无匹配（已确认当前测试零引用），全仓仅剩本文档提及；全部测试通过。

## 5. Testing Approach

- 每个缺陷一条 TDD 垂直切片（RED → GREEN），遵循 `docs/agents/development-workflow.md`。
- 修改/沿用既有测试文件（全部已存在，无新增文件）：
  - `tests/test_api_auth.py`：追加非 ASCII key → 401 用例（既有用例已覆盖 ASCII 正常/缺失/错误 key 路径）
  - `tests/test_api_routes.py`：追加 hashes 空列表 / >500 → 422 用例（不新建 `test_download_request_limits.py`）
  - `tests/test_fs_base_path_empty_guard.py`：追加 `AUTO_CREATE_DIRS=false` 且 `FS_BASE_PATH` 非空 → 不创建目录的用例
  - `tests/test_qbit_runtime_config.py`：既有 `replace_qbit_config` 全套用例跑通即确认热替换不依赖 `replace_client`，无需新增用例
  - `tests/test_error_event_type.py`：追加 crawl_error 事件 `as_dict()["type"] == "crawl_error"` 恒定性用例（该文件主题即事件 type）
  - `tests/integration/test_websocket.py`：已有 `test_websocket_ping_pong` 断言服务端 ping→pong，保持并纳入门禁
  - `tests/test_frontend_contract.py`：追加 crawl_error handler 存在性、`msg.msg || msg.message` 字段读取、30s ping 间隔与 onclose 定时器清理断言
- 全量门禁：`.venv/bin/python -m pytest tests -q`、`.venv/bin/ruff check magnet_harvester tests`、`git diff --check`。

## 6. Documentation impact

- Feature / user-facing docs introduced: none
- Materially amended existing docs: `.env.example`（`AUTO_CREATE_DIRS` 语义注释）
- Derived / memory docs invalidated: none（AGENTS.md 配置表不含 `AUTO_CREATE_DIRS`；README 未承诺 `replace_client`）

## 7. Open Questions

- 无（全部决策已批准）。
