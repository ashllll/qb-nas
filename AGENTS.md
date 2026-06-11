# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Magnet Harvester is a FastAPI-based service that crawls websites for magnet links, classifies them using MiniMax AI, and adds them to qBittorrent for NAS downloading.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌────────────────┐
│   Web UI    │────▶│  FastAPI    │────▶│  Crawler       │
│  (index.html│◀────│   (main.py) │◀────│ (crawl4ai)     │
└─────────────┘     └──────┬──────┘     └───────┬────────┘
                           │                    │
        ┌──────────────────┼────────────────────┼──────────────┐
        ▼                  ▼                    ▼              ▼
   ┌─────────┐       ┌──────────┐       ┌────────────┐  ┌──────────┐
   │  Agent  │       │Classifier│       │MagnetParser│  │ qBittorrent
   │(MiniMax)│       │(MiniMax) │       │ (regex)    │  │ Client  │
   └─────────┘       └──────────┘       └────────────┘  └──────────┘
```

**Key Components:**

- `main.py` - FastAPI server with WebSocket (`/ws`) and REST endpoints, includes `/api/config` for qB connection settings
- `crawler.py` - Crawl4AI-based web crawler with magnet link extraction and resolution filtering (2160p/4k only)
- `classifier/` - Local rule-based classification (no AI), with adult studio recognition via `studio_recognizer.py`
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
│   ├── agent.py                # Agent 对话循环
│   ├── classifier.py           # AI 分类器
│   ├── crawler.py              # Crawl4AI 爬虫
│   ├── magnet_parser.py        # 磁力链接解析（正则提取）
│   ├── qbit_client.py          # qBittorrent API
│   ├── tts_client.py           # TTS 语音通知
│   ├── config.py               # 配置（子配置拆分）
│   ├── models.py               # Pydantic 模型
│   ├── errors.py               # 错误处理
│   ├── store.py                # ItemStore（中央存储）
│   ├── bus.py                  # MessageBus（事件总线）
│   └── pipeline.py             # HarvestPipeline（管道编排）
├── tests/                      # 单元测试
├── docs/                       # 文档
├── static/                     # Web UI 静态资源
├── AGENTS.md                   # 项目说明
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
python tests/test_base64.py           # Base64 regex tests
# Or run all
python -m pytest tests/ -v
```

## Configuration

All settings are in `.env` (see `.env.example`):

- `QBIT_HOST`, `QBIT_USERNAME`, `QBIT_PASSWORD` - qBittorrent connection
- `MINIMAX_API_KEY` - Get from https://platform.minimaxi.com/user-center/basic-information/interface-key
- `PATH_*` - Download directories for each category (电影, 电视剧, 动漫, 音乐, 游戏, 软件, 综艺, 纪录片, 其他)
- `TTS_ENABLED` - Voice notifications (default: true)

## Key Implementation Details

**Crawler (`crawler.py`):**

- Uses `crawl4ai` (AsyncWebCrawler) instead of raw Playwright
- Clean markdown/text extraction via crawl4ai
- Magnet link extraction via `magnet_parser.py` (regex + Base64 decode)
- Depth-limited crawling (max 3) with crawl4ai link discovery
- `text_mode=True` blocks media resources to reduce bandwidth

**Classifier (`classifier.py`):**

- Uses Anthropic SDK with MiniMax's Codex-compatible API endpoint
- Streaming batch classification with per-item callbacks
- Local regex rules as fallback for rate limit failures
- Optional thinking-based recheck for low-confidence items (concurrency limited to 3)
- Categories: 电影, 电视剧, 动漫, 音乐, 游戏, 软件, 综艺, 纪录片, 其他

**Agent (`agent.py`):**

- Tool-based agent with 7 tools: get_stats, list_items, start_crawl, add_to_queue, reclassify_item, search_items, clear_all
- Sliding window history trimming (max 20 turns) to stay within context limits
- MAX_TURNS=8 safety limit to prevent runaway loops

**WebSocket Protocol:**

- `/ws` - Real-time magnet item updates (broadcast on discovery, classification, download status)
- `/ws/chat` - Agent conversation with streaming tokens and tool call notifications

## Common Patterns

**Adding a new API endpoint:**

1. Add Pydantic model in `models.py` if needed
2. Implement the handler in `api/routes.py` and receive runtime dependencies through `Depends(get_context)`
3. If the endpoint schedules detached async work, use the injected `BGTaskManager` path from `AppContext`
4. Use `MessageBus`/`WSBroadcaster`-driven updates rather than writing directly to websocket clients

**Modifying classification behavior:**

- Edit `LOCAL_RULES` in `classifier.py` for regex-based pre-filtering
- Adjust `SYSTEM_PROMPT` for AI classification instructions
- Categories must match keys in `settings.CATEGORY_PATHS`

**Background tasks:**
Always route detached coroutines through `BGTaskManager`, typically via the runtime dependencies injected through `AppContext`.
