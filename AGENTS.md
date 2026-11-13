# Magnet Harvester — AGENTS.md

Magnet Harvester is a FastAPI-based async service that crawls web pages for `magnet:` links, classifies them with local rule chains, and submits selected items to an aria2 bridge (下载中心) for NAS downloading. It also provides a single-page Web UI, WebSocket real-time push, Mac Agent ingest, and Agent tool interfaces.

**Current version: v3.0.0**

## Project

- **Stack**: Python 3.11（固定，见 `.python-version`）· FastAPI · Pydantic v2 · Scrapling + Playwright · httpx
- **Entry point**: `run.py`（用 `magnet_harvester.config.settings` 启动 uvicorn）
- **Config**: `.env`（模板 `.env.example`），由 pydantic-settings 解析进 `magnet_harvester/config.py`
- **License**: MIT

## Environment

- **Python 3.11**。3.15 下 curl_cffi/Playwright 有已知 SIGSEGV；`scripts/preflight.sh` 会拒绝其他版本。
- 新 worktree：`bash scripts/worktree.sh <name>`。它从 `origin/main` 起分支，复用主仓 `.venv` / `node_modules`，并复制 `.husky/_`。
- 本地 `main` 可能落后；从 `origin/main` 开工，不要碰用户未提交的改动。
- 提交时 pre-commit 自动跑 `scripts/preflight.sh --staged`；推送前跑一次完整 `bash scripts/preflight.sh`。

## Commands

所有命令在仓库根目录执行。

```bash
# Setup（按 .python-version 建 3.11 venv）
uv sync --extra dev --locked
uv run scrapling install
cp .env.example .env

# Worktree
bash scripts/worktree.sh 64-api-timeout

# Run
python run.py

# Test
python -m pytest tests -q
python -m pytest tests -q --cov   # 覆盖率门槛 79%（pyproject.toml）

# Lint & format
.venv/bin/ruff check magnet_harvester tests
.venv/bin/ruff format magnet_harvester tests

# 机器闸门（与 CI 同一套）
bash scripts/preflight.sh          # uv lock --check + ruff check/format + pytest --cov
bash scripts/preflight.sh --staged # pre-commit 用：只对 staged Python 跑 ruff check/format
npm run check                      # npm 入口：lock + lint + coverage（不含 format）
```

## Architecture

单点装配：`main.py` lifespan → `assembly.build_runtime()` → `AppContext`。服务通过构造函数注入；状态变更经 transitions → `MessageBus` → `WSBroadcaster`，没有模块级可变全局。

组件图、模块表、数据流和项目结构见 [docs/agents/architecture.md](docs/agents/architecture.md)。

## Configuration

All settings in `.env` (see `.env.example`). Key categories:

| Variable                          | Default                     | Description                                             |
| --------------------------------- | --------------------------- | ------------------------------------------------------- |
| `ARIA2_RPC_URL` / `ARIA2_SECRET` | 空（先配置） | aria2 桥接 JSON-RPC 地址与密钥 |
| `ARIA2_SAVE_PATH` | 空 | NAS 下载根目录；留空用桥接默认目录 |
| `ARIA2_SYNC_INTERVAL` | `2.0` | 下载中心状态轮询间隔（秒） |
| `SERVICE_HOST` / `SERVICE_PORT`   | `127.0.0.1` / `8899`        | Service listen address                                  |
| `AUTH_MODE`                       | `trusted`                   | `trusted`（局域网直接读写）/ `strict`（写必须带 key）  |
| `API_KEY`                         | `""` (empty = disabled)     | 程序通道 `X-API-Key`（Mac 代理）；strict 写接口要求     |
| `TRUSTED_HOSTS`                   | `""`                        | 反代/域名访问时额外允许的 Host（逗号分隔）               |
| `ALLOW_INSECURE_WRITE_API`        | `false`                     | strict 下允许无 key 写（仅本地开发）                    |
| `CORS_ALLOWED_ORIGINS`            | `""` (disabled)             | Comma-separated CORS origins                            |
| `CRAWLER_*`                       | See `.env.example`          | 超时、深度、每站并发、清晰度过滤、页面处理              |
| `FS_BASE_PATH`                    | `""`                        | Local FS root for dir creation (optional)               |
| `MIN_DISK_SPACE_GB`               | `10.0`                      | Disk warning threshold                                  |
| `SITE_COOKIES`                    | `{}`                        | JSON `{"domain": "cookie-string"}` for cookie injection |

**Security posture**: 默认 `AUTH_MODE=trusted`——局域网内读写直接放行，非 loopback 且无 `API_KEY` 只告警不拒绝启动。`AUTH_MODE=strict` 时写接口必须带正确 `X-API-Key`（未配置 key 则 fail-closed 401），且非 loopback 无 key 也无 `ALLOW_INSECURE_WRITE_API` 时拒绝启动。`/ws` 在 strict 下用握手后第一帧认证，key 不进 URL。

## Conventions

- **Python 3.11**: Use `from __future__ import annotations` in every file
- **Formatting**: ruff, line length 100 — run `ruff format` before committing
- **Types**: New code must have type annotations; Protocols for adapter seams
- **Async**: API, crawler, WebSocket, bg tasks are async; sync ops via `asyncio.to_thread`
- **DI**: Services receive dependencies via constructors; `AppContext` is the sole runtime container — no module-level mutable globals
- **Background tasks**: Use `BGTaskManager.create()`, never bare `asyncio.create_task`
- **Events**: State changes go through `MessageBus.emit()`; `WSBroadcaster` handles push — don't write directly to WebSocket. Every payload carries `updated_at` as UTC-aware ISO (`utils/timeutil.utc_now()`); never mix naive/local time into stored or API data.
- **Error logging**: Use `ErrorHandler` for structured errors; log critical exceptions with traceback
- **Code search**: 结构查询用 `grep` / `read`；本仓库不依赖代码图工具。
- **Comments**: Chinese preferred for comments, logs, error messages; docs mainly in Chinese

## Testing

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = auto` in `pyproject.toml`)
- **Test doubles**: `InMemoryItemStore` for store, `NullBus` for bus, manual `AppContext` for DI
- **Coverage**: URL 校验/SSRF、aria2 桥接与旧 qB 辅助模块、爬虫调度与单页抓取、分类规则链、状态转换/事件、API 鉴权/路由、WebSocket 广播与反压、Mac Agent ingest、存储查询；具体数量以 CI 的 pytest 输出为准
- **生产链路验证**: 自动化测试（pytest 全绿）≠ 生产验收。真实站点/下载中心/NAS 链路验证用 `scripts/smoke_production.py`（可选，见 `docs/verification.md`），两者不可互相替代
- **机器闸门**: `scripts/preflight.sh` 跑 `uv lock --check` + `ruff check` + `ruff format --check` + `pytest --cov`（覆盖率门槛 79%）；CI 还构建 Docker 镜像并对 mac_agent 跑 Swift 自测

## Key design decisions

- **Single assembly point**: `main.py` lifespan calls `build_runtime()` — all wiring in one place (see ADR-0001)
- **Phase protocols**: `CrawlPhase`, `ClassifyPhase`, `DownloadPhase` protocols enable hot-swappable implementations
- **Hot-swap downloader config**: `QBitRuntime.replace_aria2_config()` validates, persists, and swaps the client atomically
- **SSRF protection**: `url_validator` blocks loopback, link-local, multicast, RFC 1918 addresses at seed/discovered URLs; HTTP redirects are followed manually (`follow_redirects=False`) and each hop is admitted before the next request; browser subrequests are re-checked per request by `page.route`
- **DNS TOCTOU 残差**: `admit` 的解析结果没有钉到实际连接，HTTP/浏览器路径均存在 rebinding 窗口；当前接受并记录在 `docs/specs/2026-11-12-dns-toctou-residual.md`
- **No external AI**: Classification is 100% local rules — no API calls to any external service

## Agent docs

- [architecture.md](docs/agents/architecture.md) — 组件图、模块表、数据流、项目结构
- [development-workflow.md](docs/agents/development-workflow.md) — 架构/缺陷优化流程
- [github-workflow.md](docs/agents/github-workflow.md) — 本仓库的 gh/git 约束
- [issue-tracker.md](docs/agents/issue-tracker.md) — GitHub Issues 约定
- [triage-labels.md](docs/agents/triage-labels.md) — 标签词表
- [domain.md](docs/agents/domain.md) — 领域文档约定

提交、推送、关闭 issue 只在当前任务明确授权时执行；未授权先问。

## Notes

<!-- Quick-add space for per-session notes -->
