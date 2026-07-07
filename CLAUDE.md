# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Magnet Harvester is a FastAPI-based service for general-purpose magnet link crawling. It extracts magnet links from web pages, classifies them with local rules, and submits selected items to qBittorrent for NAS downloading.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌────────────────┐
│   Web UI    │────▶│  FastAPI    │────▶│  Crawler       │
│  (index.html│◀────│   (main.py) │◀────│ (Scrapling)    │
└─────────────┘     └──────┬──────┘     └───────┬────────┘
                           │                    │
        ┌──────────────────┼────────────────────┼──────────────┐
        ▼                  ▼                    ▼              ▼
   ┌─────────┐       ┌──────────┐       ┌────────────┐  ┌──────────┐
   │  Agent  │       │Classifier│       │MagnetParser│  │ qBittorrent
   │ tools   │       │local rules│      │ regex/base64│ │ Client  │
   └─────────┘       └──────────┘       └────────────┘  └──────────┘
```

**Key Components:**

- `main.py` - FastAPI server with WebSocket (`/ws`) and REST endpoints, includes `/api/config` for qB connection settings
- `crawler.py` - Scrapling-based web crawler with magnet link extraction and configurable resolution filtering
- `classifier/` - Local rule-based classification with a replaceable helper module for project-specific naming rules
- `magnet_parser.py` - Regex-based magnet link extraction from text/markdown/html
- `qbit_client.py` - qBittorrent Web API v2 client with auto-login, category creation, and default path detection
- `config.py` - Pydantic settings from `.env` file
- `store.py` - ItemStore protocol + InMemoryItemStore + FakeStore for testing
- `bus.py` - MessageBus event system with NullBus for testing

## Project Structure

```
qb-nas/
├── pyproject.toml              # 项目元数据 + 依赖声明
├── run.py                      # 入口脚本
├── magnet_harvester/           # Python 包
│   ├── __init__.py
│   ├── main.py                 # FastAPI 应用
│   ├── crawler.py              # Scrapling 爬虫
│   ├── magnet_parser.py        # 磁力链接解析（正则提取）
│   ├── qbit_client.py          # qBittorrent API
│   ├── config.py               # 配置（子配置拆分）
│   ├── models.py               # Pydantic 模型
│   ├── errors.py               # 错误处理
│   ├── store.py                # ItemStore（中央存储）
│   ├── bus.py                  # MessageBus（事件总线）
│   ├── pipeline.py             # HarvestPipeline（管道编排）
│   ├── keyword_recognizer.py   # 可替换的关键词分类辅助规则
│   └── classifier/             # 本地分类规则
│       ├── fallback.py
│       └── local_classifier.py
├── tests/                      # 单元测试
├── docs/                       # 文档
├── static/                     # Web UI 静态资源
├── CLAUDE.md                   # 项目说明
├── .env.example                # 环境变量模板
└── requirements.txt
```

## Development Commands

**Setup:**

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # Edit with your credentials
```

**Run:**

```bash
python run.py                # 推荐：入口脚本
python -m magnet_harvester   # 或使用包方式
uvicorn magnet_harvester.main:app --reload --host 0.0.0.0 --port 8899
```

**Run tests:**

```bash
python tests/test_imports.py          # Import verification
python tests/test_magnet_extract.py   # Magnet extraction tests
# Or run all
python -m pytest tests/ -v
```

## Configuration

All settings are in `.env` (see `.env.example`):

- `QBIT_HOST`, `QBIT_USERNAME`, `QBIT_PASSWORD` - qBittorrent connection
- `CRAWLER_ALLOWED_RESOLUTIONS` - Comma-separated resolution keywords to keep, default `2160p,4k`
- `FS_BASE_PATH` - Optional local filesystem root used only when the service should create directories itself
- `MIN_DISK_SPACE_GB` - Disk warning threshold

## Key Implementation Details

**Crawler (`crawler.py`):**

- Uses Scrapling `AsyncDynamicSession` instead of raw Playwright
- Clean HTML/text extraction via Scrapling
- Magnet link extraction via `magnet_parser.py` (regex + Base64 decode)
- Depth-limited crawling (max 3) with project-local detail link discovery
- `text_mode=True` blocks media resources to reduce bandwidth

**Classifier (`classifier/local_classifier.py`):**

- Pure local rule engine; no external AI dependency
- Streaming batch classification with per-item callbacks
- Uses `classifier/fallback.py` for category rules
- `keyword_recognizer.py` is a generic helper module for optional keyword-based category hints
- Categories: 电影, 电视剧, 动漫, 音乐, 游戏, 软件, 综艺, 纪录片, 其他

**Internal tool executor (`main.py`):**

- `_tool_executor()` exposes project operations such as stats, item listing, crawl start, queueing downloads, reclassification, search, and clearing state
- It operates through `ItemStore` and `HarvestPipeline`; keep new operations aligned with those boundaries

**WebSocket Protocol:**

- `/ws` - Real-time magnet item updates (broadcast on discovery, classification, download status)

## Common Patterns

**Adding a new API endpoint:**

1. Add Pydantic model in `models.py` if needed
2. Implement handler in `main.py` using existing `_bg()` helper for background tasks
3. Use `MessageBus` events to push updates to connected WebSocket clients

**Modifying classification behavior:**

- Edit `LOCAL_RULES` in `classifier/fallback.py` for regex-based local classification
- Extend or replace `keyword_recognizer.py` if project-specific naming heuristics are needed
- Categories should stay aligned with `VALID_CATEGORIES` in `classifier/fallback.py`

**Background tasks:**
Always use `_bg(coro, name)` helper in `main.py` - it wraps `asyncio.create_task` with exception logging via `add_done_callback`.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `ashllll/qb-nas`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default mattpocock/skills triage label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo: read `CONTEXT.md` at the repo root and ADRs under `docs/adr/` when present. See `docs/agents/domain.md`.
