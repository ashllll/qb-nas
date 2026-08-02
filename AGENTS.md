# Magnet Harvester — AGENTS.md

Magnet Harvester is a FastAPI-based async service that crawls web pages for `magnet:` links, classifies them with local rule chains, and submits selected items to qBittorrent via Web API for NAS downloading. It also provides a single-page Web UI, WebSocket real-time push, system clipboard monitoring, and Agent tool interfaces.

**Current version: v3.0.0**

## Project

- **Stack**: Python 3.11+ · FastAPI · Pydantic v2 · Scrapling + Playwright · httpx · pyperclip
- **Entry point**: `run.py` (invokes `uvicorn` with settings from `magnet_harvester.config.settings`)
- **Config**: `.env` file (see `.env.example`), parsed by `pydantic-settings` into `Settings` at `magnet_harvester/config.py`
- **License**: MIT

## Commands

All commands run from the project root.

```bash
# Setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # Windows
source .venv/bin/activate       # Linux/macOS
pip install -r requirements.txt
playwright install chromium
cp .env.example .env            # then edit .env with your qB credentials

# Run
python run.py
# or
uvicorn magnet_harvester.main:app --reload --host 0.0.0.0 --port 8899

# Test
python -m pytest tests -v
python -m pytest tests -q        # Quick (quiet)
python tests/test_imports.py     # Smoke test

# Lint & format (Python)
ruff check magnet_harvester tests
ruff format magnet_harvester tests

# Full check (via npm, auto-runs on pre-commit)
npm run lint     # ruff check
npm test         # pytest -q
npm run check    # lint + test
```

**Important**: npm scripts in `package.json` reference `./.venv/bin/python` (Unix path). On Windows either run the `ruff`/`pytest` commands directly or adjust the paths.

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
                    │ClipboardMon  │
                    │ pyperclip轮询 │
                    └──────────────┘
```

### Load-bearing modules

| Package/Module     | Role                                                                                                                                                                   |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `main.py`          | FastAPI app instance, lifespan (single assembly point via `build_runtime()`)                                                                                           |
| `assembly.py`      | `build_runtime()` wires all services into `AppContext`                                                                                                                 |
| `config.py`        | `Settings` (pydantic-settings) → `.env`; sub-configs: `CrawlerConfig`, `QBitConfig`, `ServiceConfig`                                                                   |
| `models.py`        | `MagnetItem`, `CrawlRequest`, `DownloadRequest`, `TaskStatus` enum, `MetricSnapshot`                                                                                   |
| `crawler.py`       | `MagnetCrawler` — Scrapling-based async crawler with depth limits, resolution filtering, cookie injection                                                              |
| `magnet_parser.py` | Regex + Base64 extraction of magnet links from text/markdown/html                                                                                                      |
| `pipeline.py`      | `HarvestPipeline` — orchestrates crawl → classify → download                                                                                                           |
| `store.py`         | `ItemStore` protocol + `InMemoryItemStore`                                                                                                                             |
| `bus.py`           | `MessageBus` event system (pub/sub), `NullBus` for testing                                                                                                             |
| `transitions.py`   | `MagnetItemTransitions` — state change + event emission                                                                                                                |
| `errors.py`        | `ErrorHandler` — deduplicated structured error aggregation                                                                                                             |
| `classifier/`      | Local rule-chain classifier (KeywordRule → StudioRule → FallbackRule)                                                                                                  |
| `qbit_client/`     | qBittorrent WebAPI v2 client (transport, mapper, paths, submitter, sync_state, stats)                                                                                  |
| `services/`        | Background and user-facing services: `QBitSyncLoop`, `UserActionExecutor`, `ItemQueryExecutor`, `ObservabilitySnapshot`, `ClipboardMonitor`, `SiteAuth`, `SystemStats` |
| `context/`         | `AppContext` (dependency container) + `QBitRuntime` (hot-swap adapter)                                                                                                 |
| `api/`             | REST routes, WebSocket broadcaster, static page router                                                                                                                 |
| `utils/`           | `url_validator` (SSRF protection), `auth`, `bg_tasks`, `serializers`                                                                                                   |

### Classification rule chain

1. **KeywordRule** (high confidence) — exact/broad keyword match from `config/category_keywords.json`
2. **StudioRule** (medium confidence) — publisher/brand recognition via `studio_recognizer.py`
3. **FallbackRule** (low confidence) — regex fallback in `fallback.py`, always returns a result

Categories: `电影, 电视剧, 动漫, 音乐, 游戏, 软件, 综艺, 纪录片, 其他`

### Data flow

1. User submits URL via UI/API → `MagnetCrawler.crawl()` → magnet links extracted
2. New items stored via `MagnetItemTransitions.found()` → `InMemoryItemStore` + `MessageBus` event
3. `HarvestPipeline._stream_classify()` runs rule chain on newly found items
4. User triggers download (or auto-download) → `QBittorrentClient.add_magnet()` with auto category/save path
5. `QBitSyncLoop` polls qB every 2s → syncs torrent state → emits events on terminal state changes
6. `WSBroadcaster` subscribes to `MessageBus` → pushes all events to WebSocket clients

## Project structure

```
qb-nas/
├── run.py                              # Entry script
├── pyproject.toml                      # Metadata, dependencies, ruff/pytest config
├── package.json                        # Husky hooks, lint-staged, prettier
├── requirements.txt                    # Runtime deps
├── .env.example                        # Environment variable template
├── config/
│   └── category_keywords.json          # Keyword classification rules
├── static/
│   └── index.html                      # Single-page Web UI
├── docs/
│   ├── adr/ADR-0001-centralized-assembly-in-lifespan.md
│   └── agents/domain.md
├── magnet_harvester/                   # Main Python package
│   ├── __init__.py
│   ├── main.py                         # FastAPI app + lifespan
│   ├── config.py                       # Settings + sub-configs
│   ├── models.py                       # Pydantic models
│   ├── crawler.py                      # Scrapling crawler
│   ├── magnet_parser.py                # Magnet link extraction (regex + Base64)
│   ├── pipeline.py                     # HarvestPipeline orchestration
│   ├── store.py                        # InMemoryItemStore
│   ├── bus.py                          # MessageBus
│   ├── transitions.py                  # MagnetItemTransitions
│   ├── errors.py                       # ErrorHandler
│   ├── assembly.py                     # build_runtime()
│   ├── api/
│   │   ├── pages.py                    # Static page router
│   │   ├── routes.py                   # REST API endpoints
│   │   └── websocket.py                # /ws WebSocket broadcaster
│   ├── classifier/
│   │   ├── __init__.py                 # Exports LocalClassifier, LOCAL_RULES, VALID_CATEGORIES
│   │   ├── local_classifier.py         # LocalClassifier + LocalClassificationEngine
│   │   ├── rule.py                     # ClassificationRule protocol, KeywordRule, StudioRule, FallbackRule
│   │   ├── keyword_recognizer.py       # KeywordCategoryRecognizer (→ config/category_keywords.json)
│   │   ├── studio_recognizer.py        # Publisher/brand recognition rules
│   │   └── fallback.py                 # LOCAL_RULES, classify_local, make_fallback
│   ├── qbit_client/
│   │   ├── __init__.py                 # Exports QBittorrentClient, TorrentStatusMapper, etc.
│   │   ├── client.py                   # QBittorrentClient facade
│   │   ├── _transport.py               # HTTP transport, login, retry
│   │   ├── mapper.py                   # qB state → TaskStatus mapping
│   │   ├── paths.py                    # Save path inference & safety
│   │   ├── stats.py                    # QBittorrentStats
│   │   ├── submitter.py                # MagnetSubmitter
│   │   └── sync_state.py               # Incremental sync state
│   ├── services/
│   │   ├── item_queries.py             # Read-only item query formatting
│   │   ├── observability.py            # Status/health/stats response snapshots
│   │   ├── clipboard_monitor.py        # ClipboardMonitor (pyperclip polling)
│   │   ├── qbit_sync.py                # QBitSyncLoop
│   │   ├── site_auth.py                # Cookie injection helper
│   │   ├── stats.py                    # SystemStats
│   │   └── user_actions.py             # UserActionExecutor
│   ├── context/
│   │   └── app_context.py              # AppContext, QBitRuntime, QBitReplacementTarget
│   └── utils/
│       ├── auth.py                     # API Key authentication dependency
│       ├── url_validator.py            # SSRF protection + URL validation
│       ├── serializers.py              # Response serializers
│       └── bg_tasks.py                 # BGTaskManager (wraps asyncio.create_task)
└── tests/                              # 80+ test files mirroring module structure
```

## Configuration

All settings in `.env` (see `.env.example`). Key categories:

| Variable                          | Default                     | Description                                             |
| --------------------------------- | --------------------------- | ------------------------------------------------------- |
| `QBIT_HOST`                       | `http://192.168.1.100:8080` | qBittorrent Web UI                                      |
| `QBIT_USERNAME` / `QBIT_PASSWORD` | `admin` / `adminadmin`      | qB credentials                                          |
| `SERVICE_HOST` / `SERVICE_PORT`   | `127.0.0.1` / `8899`        | Service listen address                                  |
| `API_KEY`                         | `""` (empty = disabled)     | Write-op auth; **required** on non-loopback             |
| `ALLOW_INSECURE_WRITE_API`        | `false`                     | Dev-only bypass for write auth                          |
| `CORS_ALLOWED_ORIGINS`            | `""` (disabled)             | Comma-separated CORS origins                            |
| `CRAWLER_*`                       | See `.env.example`          | Crawler timeout, depth, concurrency, resolution filter  |
| `FS_BASE_PATH`                    | `""`                        | Local FS root for dir creation (optional)               |
| `MIN_DISK_SPACE_GB`               | `10.0`                      | Disk warning threshold                                  |
| `SITE_COOKIES`                    | `{}`                        | JSON `{"domain": "cookie-string"}` for cookie injection |

**Security posture**: Service refuses to start on non-loopback without `API_KEY` or `ALLOW_INSECURE_WRITE_API=true`.

## Conventions

- **Python 3.11+**: Use `from __future__ import annotations` in every file
- **Formatting**: ruff, line length 100 — run `ruff format` before committing
- **Types**: New code must have type annotations; Protocols for adapter seams
- **Async**: API, crawler, WebSocket, bg tasks are async; sync ops via `asyncio.to_thread`
- **DI**: Services receive dependencies via constructors; `AppContext` is the sole runtime container — no module-level mutable globals
- **Background tasks**: Use `BGTaskManager.create()`, never bare `asyncio.create_task`
- **Events**: State changes go through `MessageBus.emit()`; `WSBroadcaster` handles push — don't write directly to WebSocket
- **Error logging**: Use `ErrorHandler` for structured errors; log critical exceptions with traceback
- **Code search**: Default to codebase-memory-mcp tools (`search_graph`, `trace_path`, `get_code_snippet`) over codegraph/grep for structural queries. Fall back to grep only for non-code text/config files.
- **Comments**: Chinese preferred for comments, logs, error messages; docs mainly in Chinese

## Testing

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = auto` in `pyproject.toml`)
- **Test doubles**: `InMemoryItemStore` for store, `NullBus` for bus, manual `AppContext` for DI
- **80+ test files** covering: URL validation/SSRF, qB client modules (transport, path, submitter, sync, mapper), crawler entry/concurrency, classifier rule chain, state transitions/events, API auth/routes, WebSocket broadcast, clipboard monitor
- **Pre-commit**: `npm run check` runs `ruff check` + `pytest -q`

## Key design decisions

- **Single assembly point**: `main.py` lifespan calls `build_runtime()` — all wiring in one place (see ADR-0001)
- **Phase protocols**: `CrawlPhase`, `ClassifyPhase`, `DownloadPhase` protocols enable hot-swappable implementations
- **Hot-swap qB config**: `QBitRuntime.replace_qbit_config()` validates, persists, and swaps the client atomically
- **SSRF protection**: `url_validator` blocks loopback, link-local, multicast, RFC 1918 addresses, plus checks redirect chains
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
