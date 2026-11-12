# Magnet Harvester — AGENTS.md

Magnet Harvester is a FastAPI-based async service that crawls web pages for `magnet:` links, classifies them with local rule chains, and submits selected items to qBittorrent via Web API for NAS downloading. It also provides a single-page Web UI, WebSocket real-time push, Mac Agent ingest, and Agent tool interfaces.

**Current version: v3.0.0**

## Project

- **Stack**: Python 3.11+ · FastAPI · Pydantic v2 · Scrapling + Playwright · httpx
- **Entry point**: `run.py` (invokes `uvicorn` with settings from `magnet_harvester.config.settings`)
- **Config**: `.env` file (see `.env.example`), parsed by `pydantic-settings` into `Settings` at `magnet_harvester/config.py`
- **License**: MIT

## Commands

All commands run from the project root.

```bash
# Setup
uv sync --extra dev --locked      # 或 python -m venv .venv && pip install -r requirements.txt
uv run scrapling install          # 抓取用 Chromium
cp .env.example .env              # 填 qB 连接信息

# Run
python run.py                     # http://localhost:8899

# Test
python -m pytest tests -q
python -m pytest tests -q --cov   # 覆盖率门槛 79%（pyproject.toml）

# Lint & format
.venv/bin/ruff check magnet_harvester tests
.venv/bin/ruff format magnet_harvester tests

# 机器闸门（与 CI 同一套）
bash scripts/preflight.sh         # uv lock --check + ruff + pytest --cov
npm run check                     # 同上，走 scripts/run-python.cjs
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌────────────────┐
│   Web UI    │────▶│  FastAPI    │────▶│  MagnetCrawler │
│ index.html  │◀────│  main.py    │◀────│   Scrapling    │
└─────────────┘     └──────┬──────┘     └───────┬────────┘
                           │                    │
        ┌──────────────────┼────────────────────┼──────────────┐
        ▼                  ▼                    ▼              ▼
   ┌───────────┐    ┌──────────────┐    ┌────────────┐  ┌──────────┐
   │ Local     │    │ SiteAuth     │    │MagnetParser│  │ qBittorrent│
   │Classifier │    │ Cookie注入    │    │ regex      │  │ Client    │
   └───────────┘    └──────────────┘    └────────────┘  └──────────┘
                           ▲
                    ┌──────────────┐
                    │  Mac Agent   │
                    │ /api/v1/ingest│
                    └──────────────┘
```

### Load-bearing modules

| Package/Module     | Role                                                                                                                                                                   |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `main.py`          | FastAPI app instance, lifespan (single assembly point via `build_runtime()`)                                                                                           |
| `assembly.py`      | `build_runtime()` wires all services into `AppContext`                                                                                                                 |
| `config.py`        | `Settings` (pydantic-settings) → `.env`; sub-configs: `CrawlerConfig`, `QBitConfig`                                                                   |
| `models.py`        | `MagnetItem`, API 请求模型, `TaskStatus` enum, `MetricSnapshot`                                                                                          |
| `crawler.py`       | `MagnetCrawler` — `MagnetPageWalker` 多页调度 + `PageFetcher` HTTP-first/浏览器回退                                                                |
| `site_walker.py`   | 多页 BFS、每站节奏、去重、SPA 壳页升级                                                                                                                    |
| `page_fetch.py`    | 单页抓取、HTTP/浏览器路径、SSRF 准入、每站限速与重试                                                                                                     |
| `jobs.py`          | `JobStore` / `AsyncJobStore` — 采集任务、事件、恢复与清理                                                                                            |
| `magnet_parser.py` | Regex + Base64 extraction of magnet links from text/markdown/html                                                                                                      |
| `pipeline.py`      | `HarvestPipeline` — orchestrates crawl → classify → download                                                                                                           |
| `store.py`         | `ItemStore` protocol + `InMemoryItemStore` / `SQLiteItemStore`（索引、keyset 分页、`get_many`）                                                |
| `bus.py`           | `MessageBus` event system (pub/sub), `NullBus` for testing                                                                                                             |
| `transitions.py`   | `DiscoveryTransitions` / `ClassificationTransitions` / `DownloadTransitions` — state change + event emission                                                                                                                |
| `errors.py`        | `ErrorHandler` — deduplicated structured error aggregation                                                                                                             |
| `classifier/`      | Local rule-chain classifier (KeywordRule → StudioRule → FallbackRule)                                                                                                  |
| `qbit_client/`     | qBittorrent WebAPI v2 client (transport, mapper, paths, submitter, sync_state, stats)                                                                                  |
| `services/`        | Background and user-facing services: `QBitSyncLoop`, `UserActionExecutor`, `ItemQueryExecutor`, `ObservabilitySnapshot`, `IngestService`, `SiteAuth`, `SystemStats` |
| `context/`         | `AppContext` (dependency container) + `QBitRuntime` (hot-swap adapter)                                                                                                 |
| `api/`             | REST routes, WebSocket broadcaster, static page router                                                                                                                 |
| `utils/`           | `url_validator` (SSRF)、`auth`、`bg_tasks`、`serializers`、`timeutil`、`infohash`                                                                    |

### Classification rule chain

1. **KeywordRule** (high confidence) — exact/broad keyword match from `config/category_keywords.json`
2. **StudioRule** (medium confidence) — publisher/brand recognition via `studio_recognizer.py`
3. **FallbackRule** (low confidence) — regex fallback in `fallback.py`, always returns a result

Categories: `电影, 电视剧, 动漫, 音乐, 游戏, 软件, 综艺, 纪录片, 其他`；StudioRule 命中「厂牌 + 日期」格式时以厂牌名作为分类（已知厂牌规范化，未知厂牌智能大小写，纯数字/过短前缀除外），此时分类为开放集合

### Data flow

1. User submits URL via UI/API → `MagnetCrawler.crawl()` → magnet links extracted
2. New items stored via `DiscoveryTransitions.found()` → `InMemoryItemStore` + `MessageBus` event
3. `HarvestPipeline._stream_classify()` runs rule chain on newly found items
4. User triggers download (or auto-download) → `QBittorrentClient.add_magnet()` with auto category/save path
5. `QBitSyncLoop` polls qB every 2s → syncs torrent state → emits events on terminal state changes
6. `WSBroadcaster` subscribes to `MessageBus` → pushes all events to WebSocket clients

**事件版本不变量**: 所有事件 payload 携带 `updated_at`（`utils/timeutil.utc_now()` 的 UTC-aware ISO 字符串，带 `+00:00`，字典序 == 时间序），前端 `item_state.js` 的 `seenAt` 版本表据此丢弃延迟到达的旧事件。不要在写库/响应里引入 naive 时间戳或混用本地时区。

## Project structure

```
qb-nas/
├── run.py                              # 入口（uvicorn + settings）
├── pyproject.toml                      # 依赖、ruff / pytest / coverage 配置
├── uv.lock  requirements.txt           # uv 锁定与 pip 运行时依赖
├── package.json                        # Husky、lint-staged、npm run check
├── config/category_keywords.json       # 关键词分类规则
├── static/                             # 无构建步骤的单页 UI
├── mac_agent/                          # macOS 菜单栏剪贴板代理（Swift）
├── deploy/                             # install.sh / verify.sh / uninstall.sh / Dockerfile
├── doc/  docs/                         # 历史规格、ADR、验收记录、架构资产
├── scripts/                            # preflight.sh、smoke_production.py、bench_crawl_parse.py
├── magnet_harvester/
│   ├── main.py  assembly.py  config.py  models.py  errors.py  logger.py
│   ├── crawler.py  site_walker.py  page_fetch.py  dynamic_page.py  magnet_sources.py
│   ├── jobs.py  pipeline.py  transitions.py  store.py  bus.py  magnet_parser.py
│   ├── api/ routes.py  ingest_routes.py  websocket.py  pages.py  security.py  error_contract.py
│   ├── classifier/ local_classifier.py  rule.py  keyword_recognizer.py  studio_recognizer.py  fallback.py
│   ├── qbit_client/ client.py  _transport.py  mapper.py  paths.py  submitter.py  sync_state.py  stats.py
│   ├── services/ ingest.py  qbit_sync.py  site_auth.py  observability.py  user_actions.py  item_queries.py  stats.py  probe.py
│   ├── context/app_context.py
│   └── utils/ auth.py  url_validator.py  serializers.py  bg_tasks.py  timeutil.py  infohash.py
└── tests/                              # 107 个测试文件、827 个用例
```

## Configuration

All settings in `.env` (see `.env.example`). Key categories:

| Variable                          | Default                     | Description                                             |
| --------------------------------- | --------------------------- | ------------------------------------------------------- |
| `QBIT_HOST`                       | `http://192.168.1.100:8080` | qBittorrent Web UI                                      |
| `QBIT_USERNAME` / `QBIT_PASSWORD` | `admin` / `adminadmin`      | qB credentials                                          |
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

- **Python 3.11+**: Use `from __future__ import annotations` in every file
- **Formatting**: ruff, line length 100 — run `ruff format` before committing
- **Types**: New code must have type annotations; Protocols for adapter seams
- **Async**: API, crawler, WebSocket, bg tasks are async; sync ops via `asyncio.to_thread`
- **DI**: Services receive dependencies via constructors; `AppContext` is the sole runtime container — no module-level mutable globals
- **Background tasks**: Use `BGTaskManager.create()`, never bare `asyncio.create_task`
- **Events**: State changes go through `MessageBus.emit()`; `WSBroadcaster` handles push — don't write directly to WebSocket
- **Error logging**: Use `ErrorHandler` for structured errors; log critical exceptions with traceback
- **Code search**: 结构性查询优先用当前环境可用的代码图工具；配置、文档与文本定位用 grep/read。
- **Comments**: Chinese preferred for comments, logs, error messages; docs mainly in Chinese

## Testing

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = auto` in `pyproject.toml`)
- **Test doubles**: `InMemoryItemStore` for store, `NullBus` for bus, manual `AppContext` for DI
- **107 个测试文件、827 个用例** 覆盖：URL 校验/SSRF、qB 客户端模块（transport、paths、submitter、sync、mapper）、爬虫调度与单页抓取、分类规则链、状态转换/事件、API 鉴权/路由、WebSocket 广播与反压、Mac Agent ingest、存储查询
- **生产链路验证**: 自动化测试（pytest 全绿）≠ 生产验收。真实站点/qB/NAS 链路验证用 `scripts/smoke_production.py`（可选，见 `docs/verification.md`），两者不可互相替代
- **机器闸门**: `scripts/preflight.sh` 跑 `uv lock --check` + `ruff check` + `ruff format --check` + `pytest --cov`（覆盖率门槛 79%）；`npm run check` 等价。CI 还构建 Docker 镜像并对 mac_agent 跑 Swift 自测

## Key design decisions

- **Single assembly point**: `main.py` lifespan calls `build_runtime()` — all wiring in one place (see ADR-0001)
- **Phase protocols**: `CrawlPhase`, `ClassifyPhase`, `DownloadPhase` protocols enable hot-swappable implementations
- **Hot-swap qB config**: `QBitRuntime.replace_qbit_config()` validates, persists, and swaps the client atomically
- **SSRF protection**: `url_validator` blocks loopback, link-local, multicast, RFC 1918 addresses at seed/discovered URLs; HTTP redirects are followed manually (`follow_redirects=False`) and each hop is admitted before the next request; browser subrequests are re-checked per request by `page.route`
- **No external AI**: Classification is 100% local rules — no API calls to any external service

## Agent Skills 自动化

本项目使用 [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) 技能包（已全局安装 24 skills）。进入项目时会自动识别当前开发阶段并加载对应技能工作流：

- **新需求 / 特性** → 先加载 `spec-driven-development`，然后 `planning-and-task-breakdown`
- **实现代码** → `incremental-implementation` + 按需 `test-driven-development`
- **审查 / 简化** → `code-review-and-quality` + `code-simplification`
- **发布部署** → `git-workflow-and-versioning` + `shiping-and-launch`

斜杠命令：`/spec` `/plan` `/build` `/test` `/review` `/code-simplify` `/ship`

架构与缺陷优化必须遵循 [docs/agents/development-workflow.md](docs/agents/development-workflow.md)：先定 seam，再按 TDD 垂直切片实施，独立复核后通过全量门禁；提交和推送始终由用户显式触发。

## Notes

<!-- Quick-add space for per-session notes -->
