# Magnet Harvester

磁力链接采集与分类服务。它会抓取网页中的 magnet 链接，用本地规则分类，并将任务发送到 qBittorrent 下载到 NAS。

## 当前能力

- 基于 `crawl4ai` 抓取页面和子链接中的 magnet
- 本地规则分类，无需外部 AI 服务
- 识别成人厂牌并为不同厂牌分配分类目录
- 通过 qBittorrent Web API 自动建分类并添加下载
- 单页 Web UI，支持实时进度、筛选、重分类和批量下载
- 提供 qBittorrent 连接配置面板和健康检查接口

## 架构

```text
┌─────────────┐     ┌─────────────┐     ┌────────────────┐
│   Web UI    │────▶│  FastAPI    │────▶│  MagnetCrawler │
│ index.html  │◀────│  main.py    │◀────│   crawl4ai     │
└─────────────┘     └──────┬──────┘     └────────────────┘
                           │
        ┌──────────────────┼───────────────────┐
        ▼                  ▼                   ▼
   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
   │ LocalClassifier│  │ MagnetParser │   │ qBittorrent  │
   │ + StudioRules │   │ regex/base64 │   │ Web API v2   │
   └──────────────┘   └──────────────┘   └──────────────┘
```

## 快速开始

### 前置条件

- Python 3.11+
- 可访问的 qBittorrent Web UI
- 首次使用 `crawl4ai` 时需要初始化浏览器

### 安装

```bash
git clone https://github.com/ashllll/qb-nas.git
cd qb-nas

python3 -m pip install -r requirements.txt

# 如果需要跑测试或开发工具
python3 -m pip install -e ".[dev]"

# 初始化 crawl4ai 浏览器环境
crawl4ai-setup

cp .env.example .env
```

### 配置

`.env` 里最常用的配置项：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `QBIT_HOST` | qBittorrent Web UI 地址 | `http://192.168.1.69:8085` |
| `QBIT_USERNAME` | qB 用户名 | `admin` |
| `QBIT_PASSWORD` | qB 密码 | 留空 |
| `SERVICE_HOST` | 服务监听地址 | `0.0.0.0` |
| `SERVICE_PORT` | 服务端口 | `8899` |
| `CRAWLER_TIMEOUT` | 单次抓取超时秒数 | `30` |
| `CRAWLER_MAX_DEPTH` | 默认最大深度 | `2` |
| `CRAWLER_CONCURRENCY` | 抓取并发数 | `3` |
| `CRAWLER_HEADLESS` | 是否无头运行 | `true` |
| `FS_BASE_PATH` | 可选，本地可写下载根目录 | 空 |
| `MIN_DISK_SPACE_GB` | 磁盘告警阈值 | `10.0` |

`FS_BASE_PATH` 只在脚本需要主动创建真实目录时使用；留空时完全依赖 qBittorrent 分类目录管理。

### 启动

```bash
python3 run.py
```

或：

```bash
uvicorn magnet_harvester.main:app --host 0.0.0.0 --port 8899
```

启动后访问 [http://localhost:8899](http://localhost:8899)。

## Web UI 使用

1. 输入要抓取的页面 URL。
2. 选择抓取深度，范围会被限制在 1 到 3。
3. 选择是否自动下载。
4. 在结果表格中筛选、重分类或批量发送到 qB。
5. 右侧配置面板可直接修改 qB 连接信息并测试连通性。

## API

### 任务与数据

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/crawl` | 发起抓取任务 |
| `POST` | `/api/download` | 批量添加选中的 hash |
| `POST` | `/api/reclassify` | 批量重新分类 |
| `GET` | `/api/items` | 列出条目，支持 `category`、`status`、`limit`、`offset` |
| `GET` | `/api/items/search` | 按关键字搜索条目 |
| `DELETE` | `/api/items` | 清空内存中的全部条目 |
| `GET` | `/api/categories` | 获取内置分类列表 |

### 状态与配置

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/status` | qB 在线状态和条目数 |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/stats` | 服务运行统计 |
| `GET` | `/api/errors` | 最近错误和错误统计 |
| `POST` | `/api/errors/clear` | 清理已标记为 resolved 的错误 |
| `GET` | `/api/config` | 获取当前 qB 连接配置 |
| `PUT` | `/api/config` | 更新 qB 连接配置并重建客户端 |
| `GET` | `/` | Web UI 页面 |
| `WebSocket` | `/ws` | 实时推送抓取、分类、下载事件 |

## 项目结构

```text
qb-nas/
├── run.py
├── pyproject.toml
├── requirements.txt
├── .env.example
├── static/
│   └── index.html
├── magnet_harvester/
│   ├── main.py
│   ├── config.py
│   ├── crawler.py
│   ├── magnet_parser.py
│   ├── models.py
│   ├── pipeline.py
│   ├── qbit_client.py
│   ├── store.py
│   ├── bus.py
│   ├── errors.py
│   ├── studio_recognizer.py
│   └── classifier/
│       ├── __init__.py
│       ├── fallback.py
│       └── local_classifier.py
└── tests/
```

## 测试

安装开发依赖后运行：

```bash
python3 -m pytest tests -q
```

也可以运行部分脚本：

```bash
python3 tests/test_imports.py
python3 tests/test_pipeline_phases.py
python3 tests/test_error_handler.py
```

## 实现说明

- `magnet_harvester/crawler.py`：基于 `crawl4ai` 抽取页面文本和子链接
- `magnet_harvester/magnet_parser.py`：从文本、HTML、属性值和 Base64 中提取 magnet
- `magnet_harvester/classifier/local_classifier.py`：本地正则分类，优先使用厂牌识别
- `magnet_harvester/qbit_client.py`：管理 qB 登录、重试、分类目录和下载添加
- `magnet_harvester/pipeline.py`：编排抓取、分类、下载三阶段

## License

MIT
