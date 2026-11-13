# Architecture

单点装配：`main.py` lifespan → `assembly.build_runtime()` → `AppContext`。服务通过构造函数注入；状态变更经 transitions → `MessageBus` → `WSBroadcaster`，没有模块级可变全局。

## 组件图

```text
┌─────────────┐     ┌─────────────┐     ┌────────────────┐
│   Web UI    │────▶│  FastAPI    │────▶│  MagnetCrawler │
│ index.html  │◀────│  main.py    │◀────│   Scrapling    │
└─────────────┘     └──────┬──────┘     └───────┬────────┘
                           │                    │
        ┌──────────────────┼────────────────────┼──────────────┐
        ▼                  ▼                    ▼              ▼
   ┌───────────┐    ┌──────────────┐    ┌────────────┐  ┌──────────┐
   │ Local     │    │ SiteAuth     │    │MagnetParser│  │ Aria2    │
   │Classifier │    │ Cookie注入    │    │ regex      │  │ Bridge   │
   └───────────┘    └──────────────┘    └────────────┘  └──────────┘
                           ▲
                    ┌──────────────┐
                    │   Mac Agent  │
                    │ /api/v1/ingest│
                    └──────────────┘
```

## Load-bearing modules

| Package/Module     | Role |
| ------------------ | ---- |
| `main.py`          | FastAPI app instance, lifespan（唯一装配点 `build_runtime()`） |
| `assembly.py`      | `build_runtime()` 把服务装配进 `AppContext` |
| `config.py`        | `Settings`（pydantic-settings）→ `.env`；子配置 `CrawlerConfig`、`Aria2Config`、`QBitConfig` |
| `models.py`        | `MagnetItem`、API 请求模型、`TaskStatus`、`MetricSnapshot` |
| `crawler.py`       | `MagnetCrawler` — `MagnetPageWalker` 多页调度 + `PageFetcher` HTTP-first/浏览器回退 |
| `site_walker.py`   | 多页 BFS、每站节奏、去重、SPA 壳页升级 |
| `page_fetch.py`    | 单页抓取、HTTP/浏览器路径、SSRF 准入、每站限速与重试 |
| `jobs.py`          | `JobStore` / `AsyncJobStore` — 采集任务、事件、恢复与清理 |
| `aria2_client.py`  | fnOS Aria2 桥接提交、GID 持久化与状态同步 |
| `magnet_parser.py` | 磁力链接提取（正则 + Base64） |
| `pipeline.py`      | `HarvestPipeline` — 采集 → 分类 → 下载编排 |
| `store.py`         | `ItemStore` protocol + `InMemoryItemStore` / `SQLiteItemStore`（索引、keyset 分页、`get_many`） |
| `bus.py`           | `MessageBus` 事件系统；`NullBus` 用于测试 |
| `transitions.py`   | `DiscoveryTransitions` / `ClassificationTransitions` / `DownloadTransitions` — 状态变更 + 事件 |
| `errors.py`        | `ErrorHandler` — 去重的结构化错误聚合 |
| `classifier/`      | 本地规则链（KeywordRule → StudioRule → FallbackRule） |
| `qbit_client/`     | 旧 qB WebAPI 客户端；状态映射、路径与同步辅助代码仍被 aria2 链路复用 |
| `services/`        | `QBitSyncLoop`、`UserActionExecutor`、`ItemQueryExecutor`、`ObservabilitySnapshot`、`IngestService`、`SiteAuth`、`SystemStats` |
| `context/`         | `AppContext` 依赖容器 + `QBitRuntime` 热替换适配器 |
| `api/`             | REST 路由、WebSocket 广播器、静态页路由 |
| `utils/`           | `url_validator`（SSRF）、`auth`、`bg_tasks`、`serializers`、`timeutil`、`infohash` |

## Classification rule chain

1. **KeywordRule**（高置信）— `config/category_keywords.json` 的关键词精确/宽泛匹配
2. **StudioRule**（中置信）— `studio_recognizer.py` 的厂牌/品牌识别
3. **FallbackRule**（低置信）— `fallback.py` 的正则回退，始终返回结果

分类：`电影, 电视剧, 动漫, 音乐, 游戏, 软件, 综艺, 纪录片, 其他`；StudioRule 命中「厂牌 + 日期」时以厂牌名作为分类（已知厂牌规范化，未知厂牌智能大小写，纯数字/过短前缀除外），此时分类为开放集合。

## Data flow

1. 用户通过 UI/API 提交 URL → `MagnetCrawler.crawl()` → 提取磁力链接
2. 新条目经 `DiscoveryTransitions.found()` → `ItemStore` + `MessageBus` 事件
3. `HarvestPipeline._stream_classify()` 对新条目跑规则链
4. 用户点下载（或自动下载）→ aria2 桥接 `addUri`，带自动分类/保存路径
5. `QBitSyncLoop` 每 2 秒轮询下载桥接 → 同步任务状态 → 终态变更时发事件
6. `WSBroadcaster` 订阅 `MessageBus` → 把事件推给 WebSocket 客户端

## Project structure

```text
qb-nas/
├── run.py                              # 入口（uvicorn + settings）
├── pyproject.toml                      # 依赖、ruff / pytest / coverage 配置
├── uv.lock  requirements.txt           # uv 锁定与 pip 运行时依赖
├── package.json                        # Husky、lint-staged、npm run check
├── .python-version                     # 3.11（本机与 CI 固定版本）
├── config/category_keywords.json       # 关键词分类规则
├── static/                             # 无构建步骤的单页 UI
├── mac_agent/                          # macOS 菜单栏剪贴板代理（Swift）
├── deploy/                             # install.sh / verify.sh / uninstall.sh / Dockerfile
├── doc/  docs/                         # 历史规格、ADR、验收记录、架构资产
├── scripts/                            # preflight.sh、worktree.sh、smoke_production.py、bench_crawl_parse.py
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
└── tests/                              # pytest + pytest-asyncio；具体数量以 CI 输出为准
```
