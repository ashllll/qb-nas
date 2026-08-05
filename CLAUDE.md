# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Magnet Harvester is a FastAPI-based async service that crawls web pages for `magnet:` links, classifies them with local rule chains, and submits selected items to qBittorrent via Web API for NAS downloading. It also provides a single-page Web UI, WebSocket real-time push, system clipboard monitoring, and Agent tool interfaces.

**Current version: v3.0.0** — the codebase has been fully refactored into cohesive modules wired by `assembly.build_runtime()`; there are no module-level mutable globals.

## Architecture

```text
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

All components are assembled in one place (`assembly.build_runtime()` → `AppContext`), services receive dependencies via constructors, and state changes flow through `MagnetItemTransitions` → `MessageBus` → `WSBroadcaster` → WebSocket clients.

## Key Components

| Module                   | Role                                                                                                                              |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| `main.py`                | FastAPI app + lifespan (single assembly point)                                                                                    |
| `assembly.py`            | `build_runtime()` wires all services into `AppContext`                                                                            |
| `config.py`              | Pydantic `Settings` from `.env` (sub-configs: CrawlerConfig, QBitConfig, ServiceConfig)                                           |
| `models.py`              | `MagnetItem`, `CrawlRequest`, `DownloadRequest`, `TaskStatus`, `MetricSnapshot`                                                   |
| `crawler.py`             | `MagnetCrawler` — Scrapling Spider event adapter (concurrency, depth, resolution filter, cookie injection)                        |
| `scrapling_spider.py`    | Scrapling Spider scheduling + browser network admission policy (SSRF guard)                                                       |
| `magnet_parser.py`       | Regex + Base64 extraction of magnet links                                                                                         |
| `pipeline.py`            | `HarvestPipeline` — crawl → classify → download orchestration                                                                     |
| `transitions.py`         | State transition domains (Discovery / Classification / Download) + event emission                                                 |
| `store.py`               | `ItemStore` protocol + `InMemoryItemStore` (+ sqlite backend)                                                                     |
| `bus.py`                 | `MessageBus` pub/sub, `NullBus` for tests                                                                                         |
| `errors.py`              | `ErrorHandler` — deduplicated structured errors                                                                                   |
| `classifier/`            | Rule chain: `KeywordRule` → `StudioRule` → `FallbackRule`; LRU result cache with real stats                                       |
| `qbit_client/`           | qBittorrent WebAPI v2 client (transport, mapper, paths, submitter, sync_state, stats)                                             |
| `services/`              | `QBitSyncLoop`, `UserActionExecutor`, `ItemQueryExecutor`, `ObservabilitySnapshot`, `ClipboardMonitor`, `SiteAuth`, `SystemStats` |
| `context/app_context.py` | `AppContext` (DI container), `QBitRuntime` (hot-swap adapter depending only on `QBitReplacementTarget`)                           |
| `api/`                   | REST routes, WebSocket broadcaster (API-key handshake auth), static page router                                                   |
| `utils/`                 | `url_validator` (SSRF), `auth` (API key), `bg_tasks`, `serializers`                                                               |

## Project Structure

```text
qb-nas/
├── run.py                              # Entry script
├── pyproject.toml                      # Metadata, dependencies, ruff/pytest config
├── requirements.txt                    # Runtime deps (pip path)
├── uv.lock                             # uv locked deps
├── config/category_keywords.json       # Keyword classification rules
├── static/                             # Single-page Web UI (index.html, styles.css, api_client.js, item_state.js, app.js)
├── scripts/smoke_production.py         # Optional real-environment smoke verification
├── docs/                               # verification.md, adr/, agents/, specs/
├── magnet_harvester/
│   ├── main.py  assembly.py  config.py  models.py  errors.py
│   ├── crawler.py  scrapling_spider.py  dynamic_page.py  magnet_sources.py
│   ├── magnet_parser.py  logger.py  pipeline.py
│   ├── transitions.py  store.py  bus.py
│   ├── api/ routes.py  websocket.py  pages.py
│   ├── classifier/ local_classifier.py  rule.py  keyword_recognizer.py  studio_recognizer.py  fallback.py
│   ├── qbit_client/ client.py  _transport.py  mapper.py  paths.py  submitter.py  sync_state.py  stats.py
│   ├── services/ qbit_sync.py  clipboard_monitor.py  site_auth.py  observability.py  user_actions.py  item_queries.py  stats.py
│   ├── context/ app_context.py
│   └── utils/ auth.py  url_validator.py  serializers.py  bg_tasks.py
└── tests/                              # 80+ files mirroring module structure
```

## Development Commands

```bash
# Setup
uv sync --extra dev --locked            # or: pip install -r requirements.txt
uv run scrapling install                # installs Chromium for Scrapling
cp .env.example .env                    # then edit with your qB credentials

# Run
python run.py

# Test
python -m pytest tests -v
python -m pytest tests -q               # Quick

# Lint & format
.venv/bin/ruff check magnet_harvester tests
.venv/bin/ruff format magnet_harvester tests
```

## Configuration

All settings live in `.env` (see `.env.example`). Key categories:

- `QBIT_HOST` / `QBIT_USERNAME` / `QBIT_PASSWORD` / `QBIT_SYNC_INTERVAL` — qBittorrent connection
- `SERVICE_HOST` / `SERVICE_PORT` / `API_KEY` / `ALLOW_INSECURE_WRITE_API` — service & auth
- `CRAWLER_*` — crawler timeout, depth, concurrency, resolution filter, page processing
- `FS_BASE_PATH` / `MIN_DISK_SPACE_GB` — filesystem & disk warnings
- `SITE_COOKIES` — JSON `{"domain": "cookie-string"}` for cookie injection
- `LOG_LEVEL` / `LOG_FILE` — logging; `STORE_BACKEND` / `STORE_DB_PATH` — storage

**Security posture**: the service refuses to start on non-loopback without `API_KEY` or `ALLOW_INSECURE_WRITE_API=true`. The `/ws` endpoint performs the same API-key check at handshake (key passed as query param; disabled when `API_KEY` is empty).

## Key Implementation Details

**Crawler (`crawler.py` + `scrapling_spider.py`):**

- Scrapling `Spider.stream()` is the sole multi-page scheduling entry (queue, concurrency, per-domain throttle, retry, depth, dedup, robots.txt)
- `CrawlTargetAdmission` runs at seed URL, followed URLs, and final response URLs; browser navigation/subresource/WebSocket requests are admitted before dispatch, Service Workers disabled
- `MagnetSourceExtractor` handles magnet business parsing; resolution filtering keeps `CRAWLER_ALLOWED_RESOLUTIONS`

**Classifier (`classifier/local_classifier.py`):**

- Rule chain: `KeywordRule` (high confidence) → `StudioRule` (medium) → `FallbackRule` (low, always returns)
- `LocalClassificationEngine` owns a thread-safe LRU cache (max 1024) with real `get_cache_stats()`/`clear_cache()`; `reload_rules()` invalidates cache
- Categories: 电影, 电视剧, 动漫, 音乐, 游戏, 软件, 综艺, 纪录片, 其他

**State transitions (`transitions.py`):**

- Three deep domains: `DiscoveryTransitions`, `ClassificationTransitions`, `DownloadTransitions`; callers depend only on the lifecycle module they use
- Transitions guard source states (e.g. `classified()` only applies from `classifying`) — do not weaken these guards
- Every event payload carries `updated_at` (naive-local `datetime.now()` ISO; lexicographic == chronological). The frontend `seenAt` version table in `static/item_state.js` drops late-arriving stale events. **Never introduce aware-UTC or mixed timezones.**

**qB hot-swap (`context/app_context.py`):**

- `QBitRuntime` depends ONLY on `QBitReplacementTarget` (qbit/pipeline/qbit_sync/observability + `on_qbit_replaced` callback), built via `AppContext.replacement_target()` / `QBitRuntime.from_context(ctx)`
- `replace_qbit_config()` validates, persists, and swaps atomically with rollback on failure

**WebSocket protocol:**

- `/ws` — init snapshot → real-time item updates (magnet_found, store_changed, classify_done, download_result, …); ping → pong
- Authentication happens BEFORE `ws.accept()`; wrong/missing key → close 4401

## Common Patterns

**Adding a new API endpoint:**

1. Add Pydantic model in `models.py` if needed
2. Implement handler in `api/routes.py` using `Depends(get_context)`; use `BGTaskManager` (via `ctx.runtime.bg_manager`) for background work — never bare `asyncio.create_task`
3. Emit `MessageBus` events to push updates to WebSocket clients

**Modifying classification behavior:**

- Extend `classifier/rule.py` rules or `config/category_keywords.json` keywords
- Categories stay aligned with `VALID_CATEGORIES` in `classifier/fallback.py`
- After changing rules, remember `reload_rules()` invalidates the classification cache

**Hot-swapping qB config:** keep `QBitRuntime` decoupled — any new dependent that needs the new client must be added to `QBitReplacementTarget` (and `_commit_runtime`/`_rollback_runtime`), never reach into `AppContext` from `QBitRuntime`.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `ashllll/qb-nas`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default mattpocock/skills triage label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo: read `CONTEXT.md` at the repo root and ADRs under `docs/adr/` when present. See `docs/agents/domain.md`.
