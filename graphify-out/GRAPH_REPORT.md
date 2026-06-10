# Graph Report - . (2026-06-10)

## Corpus Check

- Corpus is ~14,446 words - fits in a single context window. You may not need a graph.

## Summary

- 500 nodes · 1258 edges · 15 communities (11 shown, 4 thin omitted)
- Extraction: 72% EXTRACTED · 28% INFERRED · 0% AMBIGUOUS · INFERRED: 354 edges (avg confidence: 0.52)
- Token cost: 33,198 input · 33,198 output

## Community Hubs (Navigation)

- [[_COMMUNITY_Event Bus & Messaging|Event Bus & Messaging]]
- [[_COMMUNITY_qBittorrent Integration|qBittorrent Integration]]
- [[_COMMUNITY_Local Classification Rules|Local Classification Rules]]
- [[_COMMUNITY_Configuration & Crawler|Configuration & Crawler]]
- [[_COMMUNITY_Item Store & Persistence|Item Store & Persistence]]
- [[_COMMUNITY_FastAPI Server & APIs|FastAPI Server & APIs]]
- [[_COMMUNITY_Documentation & Architecture|Documentation & Architecture]]
- [[_COMMUNITY_Magnet Parsing & Filtering|Magnet Parsing & Filtering]]
- [[_COMMUNITY_Error Handling|Error Handling]]
- [[_COMMUNITY_qBittorrent Path Safety|qBittorrent Path Safety]]
- [[_COMMUNITY_Agent Workflow Labels|Agent Workflow Labels]]
- [[_COMMUNITY_Claude Settings|Claude Settings]]
- [[_COMMUNITY_Category Keywords Config|Category Keywords Config]]
- [[_COMMUNITY_Package Initialization|Package Initialization]]
- [[_COMMUNITY_Pydantic Dependencies|Pydantic Dependencies]]

## God Nodes (most connected - your core abstractions)

1. `MagnetItem` - 53 edges
2. `QBittorrentClient` - 53 edges
3. `TaskStatus` - 52 edges
4. `HarvestPipeline` - 43 edges
5. `Event` - 40 edges
6. `MagnetCrawler` - 40 edges
7. `MessageBus` - 39 edges
8. `EventType` - 37 edges
9. `InMemoryItemStore` - 37 edges
10. `LocalClassifier` - 34 edges

## Surprising Connections (you probably didn't know these)

- `Magnet Harvester (AGENTS.md)` --semantically_similar_to--> `Magnet Harvester (CLAUDE.md)` [INFERRED] [semantically similar]
  AGENTS.md → CLAUDE.md
- `FastAPI Dependency` --semantically_similar_to--> `FastAPI Server` [INFERRED] [semantically similar]
  requirements.txt → AGENTS.md
- `MagnetParser (regex/base64)` --semantically_similar_to--> `Crawl4AI-based Web Crawler` [INFERRED] [semantically similar]
  README.md → AGENTS.md
- `crawl4ai Dependency` --semantically_similar_to--> `Crawl4AI-based Web Crawler` [INFERRED] [semantically similar]
  requirements.txt → AGENTS.md
- `MiniMax AI Classifier` --semantically_similar_to--> `Local Rule-based Classifier` [INFERRED] [semantically similar]
  AGENTS.md → CLAUDE.md

## Import Cycles

- 1-file cycle: `magnet_harvester/main.py -> magnet_harvester/main.py`

## Hyperedges (group relationships)

- **Classifier Architecture Evolution (MiniMax AI -> Local Rules)** — agents_minimax_classifier, claude_local_classifier, readme_localclassifier, context_local_classification, context_keywordcategoryrecognizer [INFERRED 0.85]
- **WebSocket Real-time Update System** — static_websocket_client, static_task_status_icons, claude_messagebus, context_download_state_sync, context_torrentstatusmapper [INFERRED 0.85]
- **Agent Skill Documentation Ecosystem** — domain_single_context_repo, issue_tracker_github, triage_labels_vocabulary, domain_adr_conflict_rule [EXTRACTED 1.00]

## Communities (15 total, 4 thin omitted)

### Community 0 - "Event Bus & Messaging"

Cohesion: 0.08
Nodes (48): CrawlRequest, DownloadRequest, Enum, Event, EventType, MessageBus, NullBus, MessageBus — 类型化事件总线 接口: emit(event) — 异步 fan-out 到所有订阅者。 适配器: NullBus — 测试用静默总 (+40 more)

### Community 1 - "qBittorrent Integration"

Cohesion: 0.07
Nodes (28): AppContext, AsyncClient, FastAPI, QBitConfig, AppContext, get_context(), 更新 qBittorrent 连接配置并重建客户端, RuntimeContext (+20 more)

### Community 2 - "Local Classification Rules"

Cohesion: 0.05
Nodes (40): classify_local(), make_fallback(), Local fallback classification rules — 独立于 API 调用的本地分类, 生成兜底分类结果（save_path 为空，由下载时动态解析）, LocalClassifier — 本地规则分类器 子模块: - fallback: 本地分类规则（LOCAL_RULES + 辅助函数） - local_c, LocalClassifier, \_NullUsageStats, LocalClassifier — 纯本地规则分类器，零外部依赖 直接使用 LOCAL_RULES 正则进行分类，同步 API。 符合 ClassifyPha (+32 more)

### Community 3 - "Configuration & Crawler"

Cohesion: 0.07
Nodes (26): BaseSettings, CrawlerConfig, CrawlerConfig, 动态更新 qB 配置（由前端配置面板调用）, ServiceConfig, Settings, CrawlMetrics, MagnetCrawler (+18 more)

### Community 4 - "Item Store & Persistence"

Cohesion: 0.06
Nodes (23): InMemoryItemStore, ItemStore, MagnetItem, ItemStore — 磁力链接中央存储（深模块） 可替换适配器： - InMemoryItemStore: 默认内存实现，用于单进程 - RedisItem, 支持通过 hash 前缀查找完整 hash（Agent 用）, ItemStore 协议 — 所有 store 适配器必须实现此接口, ItemStore 的默认内存适配器。 接口：add / get / update / remove / list / search / clear, 添加条目，已存在返回 False（全局去重） (+15 more)

### Community 5 - "FastAPI Server & APIs"

Cohesion: 0.08
Nodes (36): BaseModel, Lock, \_bg(), clear_items(), download_selected(), \_emit_store_changed(), \_ensure_qbit_lock(), \_format_uptime() (+28 more)

### Community 6 - "Documentation & Architecture"

Cohesion: 0.06
Nodes (43): Agent Tool-based Executor, Crawl4AI-based Web Crawler, FastAPI Server, Magnet Harvester (AGENTS.md), MiniMax AI Classifier, TTS Voice Notification Client, WebSocket Chat Endpoint (/ws/chat), HarvestPipeline (+35 more)

### Community 7 - "Magnet Parsing & Filtering"

Cohesion: 0.10
Nodes (30): filter_resolution_items(), 按分辨率过滤磁力列表，只保留含指定分辨率关键词的条目, extract_from_text(), parse_magnet(), MagnetParser — 磁力链接解析与提取工具 从文本中提取磁力链接，支持： - 标准 magnet:?xt=urn:btih:... 格式 - Bas, 从文本中提取所有磁力链接（主入口函数） 支持三种模式： 1. 标准 magnet: 格式 2. Base64 编码的磁力链接, 将单个磁力链接字符串解析为结构化数据 返回: { "hash": "infohash (大写)",, 尝试从文本中解码 Base64 编码的磁力链接 (+22 more)

### Community 8 - "Error Handling"

Cohesion: 0.13
Nodes (14): Exception, ErrorCategory, ErrorHandler, ErrorRecord, Any, 测试 ErrorHandler — 验证可独立实例化、无单例, 两个 ErrorHandler 实例互不干扰, ErrorHandler() 每次都创建新实例 (+6 more)

### Community 9 - "qBittorrent Path Safety"

Cohesion: 0.14
Nodes (7): qBittorrent WebAPI v2 客户端 v2.0, 把分类名压成单个本地路径段，避免 FS_BASE_PATH 下目录穿越。, \_safe_fs_segment(), TorrentStatusMapper, test_safe_fs_segment_blocks_path_traversal(), test_safe_fs_segment_falls_back_for_empty_name(), 测试 qB 状态到 TaskStatus 的映射

### Community 10 - "Agent Workflow Labels"

Cohesion: 0.33
Nodes (6): GitHub Issue Tracker, ashllll/qb-nas Repository, Triage Label Vocabulary, needs-triage Label, ready-for-agent Label, ready-for-human Label

## Knowledge Gaps

- **19 isolated node(s):** `PreToolUse`, `_note`, `keywords`, `Any`, `Exception` (+14 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions

_Questions this graph is uniquely positioned to answer:_

- **Why does `LocalClassifier` connect `Local Classification Rules` to `Event Bus & Messaging`, `qBittorrent Integration`, `Configuration & Crawler`, `FastAPI Server & APIs`?**
  _High betweenness centrality (0.129) - this node is a cross-community bridge._
- **Why does `QBittorrentClient` connect `qBittorrent Integration` to `Event Bus & Messaging`, `qBittorrent Path Safety`, `Configuration & Crawler`, `FastAPI Server & APIs`?**
  _High betweenness centrality (0.128) - this node is a cross-community bridge._
- **Why does `TaskStatus` connect `Event Bus & Messaging` to `qBittorrent Integration`, `Item Store & Persistence`, `FastAPI Server & APIs`, `qBittorrent Path Safety`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Are the 34 inferred relationships involving `MagnetItem` (e.g. with `AppContext` and `CrawlRequest`) actually correct?**
  _`MagnetItem` has 34 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `QBittorrentClient` (e.g. with `AppContext` and `CrawlRequest`) actually correct?**
  _`QBittorrentClient` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 40 inferred relationships involving `TaskStatus` (e.g. with `AsyncClient` and `CrawlRequest`) actually correct?**
  _`TaskStatus` has 40 INFERRED edges - model-reasoned connections that need verification._
- **Are the 25 inferred relationships involving `HarvestPipeline` (e.g. with `AppContext` and `CrawlRequest`) actually correct?**
  _`HarvestPipeline` has 25 INFERRED edges - model-reasoned connections that need verification._
