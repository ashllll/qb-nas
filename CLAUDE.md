# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Magnet Harvester is a FastAPI-based service that crawls websites for magnet links, classifies them using MiniMax AI, and adds them to qBittorrent for NAS downloading.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Web UI    │────▶│  FastAPI    │────▶│  Crawler    │
│  (index.html│◀────│   (main.py) │◀────│ (Playwright)│
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
   ┌─────────┐       ┌──────────┐      ┌──────────┐
   │  Agent  │       │Classifier│      │  qBittorrent
   │(MiniMax)│       │(MiniMax) │      │  Client  │
   └─────────┘       └──────────┘      └──────────┘
```

**Key Components:**
- `main.py` - FastAPI server with WebSocket (`/ws`, `/ws/chat`) and REST endpoints
- `crawler.py` - Playwright-based magnet extraction with 6 strategies (href, innerHTML, onclick, script, pagination, lazy-load)
- `classifier.py` - MiniMax AI content classification with streaming batch processing and optional thinking-based recheck for low-confidence items
- `agent.py` - Natural language agent using tool_use for hands-free control
- `qbit_client.py` - qBittorrent Web API v2 client with auto-login
- `tts_client.py` - Voice notifications for completed operations
- `config.py` - Pydantic settings from `.env` file

## Development Commands

**Setup:**
```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # Edit with your credentials
```

**Run:**
```bash
python main.py              # Default: http://0.0.0.0:8899
uvicorn main:app --reload --host 0.0.0.0 --port 8899
```

**No tests/lint configured** - this is a single-user utility project without CI/CD.

## Configuration

All settings are in `.env` (see `.env.example`):
- `QBIT_HOST`, `QBIT_USERNAME`, `QBIT_PASSWORD` - qBittorrent connection
- `MINIMAX_API_KEY` - Get from https://platform.minimaxi.com/user-center/basic-information/interface-key
- `PATH_*` - Download directories for each category (电影, 电视剧, 动漫, 音乐, 游戏, 软件, 综艺, 纪录片, 其他)
- `TTS_ENABLED` - Voice notifications (default: true)

## Key Implementation Details

**Crawler (`crawler.py`):**
- Uses `playwright-stealth` to bypass anti-bot detection
- 6 extraction strategies: href attributes, full HTML, onclick/data-* attributes, script tags, pagination clicks, scroll lazy-loading
- Depth-limited crawling (max 3) with same-domain link following
- Media (images/fonts) blocked to reduce bandwidth

**Classifier (`classifier.py`):**
- Uses Anthropic SDK with MiniMax's Claude-compatible API endpoint
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
2. Implement handler in `main.py` using existing `_bg()` helper for background tasks
3. Use `broadcast()` to push updates to connected WebSocket clients

**Modifying classification behavior:**
- Edit `LOCAL_RULES` in `classifier.py` for regex-based pre-filtering
- Adjust `SYSTEM_PROMPT` for AI classification instructions
- Categories must match keys in `settings.CATEGORY_PATHS`

**Background tasks:**
Always use `_bg(coro, name)` helper in `main.py` - it wraps `asyncio.create_task` with exception logging via `add_done_callback`.
