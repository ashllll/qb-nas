# Magnet Harvester 🔗

**磁力链接采集与分类服务** — 自动爬取网站磁力链接，使用 **MiniMax AI** 智能分类，自动添加到 **qBittorrent** 下载至 NAS。

## 架构

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

## 功能特性

- 🔍 **网页爬取** — 基于 [crawl4ai](https://github.com/unclecode/crawl4ai) 引擎，自动发现磁力链接
- 🤖 **本地规则分类** — 无需 API Key，基于正则规则的智能分类（电影、电视剧、动漫、音乐等 9 类）
- 💬 **自然语言 Agent** — 通过 WebSocket 聊天界面，用自然语言控制爬虫
- 📥 **自动下载** — 分类后自动添加到 qBittorrent 下载队列
- 🔔 **语音通知** — 操作完成后 TTS 语音播报（可选）
- 🌐 **Web UI** — 实时 WebSocket 推送，所见即所得

## 快速开始

### 前置条件

- Python ≥ 3.11
- 运行中的 [qBittorrent](https://www.qbittorrent.org/)（需启用 Web UI）

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/qb-nas.git
cd qb-nas

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 初始化 crawl4ai 浏览器引擎
crawl4ai-setup

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 QBIT_HOST（如需 Agent 对话功能还需 MINIMAX_API_KEY）
```

### 配置

编辑 `.env` 文件，主要配置项：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `QBIT_HOST` | qBittorrent Web UI 地址 | `http://192.168.1.69:8085` |
| `QBIT_USERNAME` | qBittorrent 用户名 | `admin` |
| `QBIT_PASSWORD` | qBittorrent 密码 | |
| `MINIMAX_API_KEY` | MiniMax API 密钥（仅 Agent 对话需要） | 可选 |
| `SERVICE_PORT` | 服务端口 | `8899` |
| `CRAWLER_TIMEOUT` | 爬虫超时（秒） | `30` |
| `CRAWLER_MAX_DEPTH` | 最深爬取层级 | `2` |
| `PATH_MOVIE` | 电影下载路径 | `/vol2/1000/downloads/电影` |
| ... | 更多分类路径 | 请在 `.env` 中查看 |

### 启动

```bash
# 方式一：入口脚本（推荐）
python run.py

# 方式二：直接 uvicorn
uvicorn magnet_harvester.main:app --host 0.0.0.0 --port 8899

# 方式三：开发模式（热重载）
uvicorn magnet_harvester.main:app --reload --host 0.0.0.0 --port 8899
```

启动后浏览器访问 **http://localhost:8899** 打开 Web UI。

## 使用方式

### Web UI

在浏览器中打开 `http://localhost:8899`：

1. **输入 URL** — 在输入框输入要爬取的网站地址
2. **设置深度** — 选择是否深度爬取子页面（1-3 层）
3. **自动下载** — 勾选后分类完成自动添加至 qBittorrent
4. **实时看板** — 爬取进度、分类结果、下载状态实时推送

### 聊天 Agent

通过 WebSocket 聊天界面（`/ws/chat`）使用自然语言控制，例如：

- "帮我从 xxx.com 爬取磁力链接"
- "把最近分类为电影的全部下载"
- "显示当前的统计信息"
- "重新分类所有未分类的项目"
- "清空所有条目"

### API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stats` | 获取统计信息 |
| POST | `/api/crawl` | 提交爬取任务 |
| GET | `/api/items` | 获取条目列表 |
| GET | `/api/items/search` | 搜索条目 |
| POST | `/api/items/reclassify` | 重新分类 |
| POST | `/api/items/download` | 下载条目 |
| DELETE | `/api/items/clear` | 清空所有条目 |
| WebSocket | `/ws` | 实时事件推送 |
| WebSocket | `/ws/chat` | Agent 对话 |

## 项目结构

```
qb-nas/
├── run.py                      # 入口脚本
├── pyproject.toml              # 项目元数据 + 依赖
├── requirements.txt            # pip 依赖声明
├── install.sh                  # Linux 安装脚本
├── magnet_harvester/           # Python 包
│   ├── main.py                 # FastAPI 应用 + API 路由
│   ├── agent.py                # 自然语言 Agent 对话循环
│   ├── classifier.py           # MiniMax AI 分类器
│   ├── crawler.py              # crawl4ai 爬虫适配器
│   ├── magnet_parser.py        # 磁力链接正则提取器
│   ├── qbit_client.py          # qBittorrent Web API 客户端
│   ├── tts_client.py           # TTS 语音通知
│   ├── config.py               # 配置管理（Pydantic Settings）
│   ├── models.py               # Pydantic 数据模型
│   ├── errors.py               # 统一错误处理
│   ├── store.py                # 内存存储（ItemStore）
│   ├── bus.py                  # 事件总线（MessageBus）
│   └── pipeline.py             # 管道编排（爬取→分类→下载）
├── tests/                      # 测试
├── static/                     # Web UI 静态资源
│   └── index.html              # 单页应用
└── .env.example                # 环境变量模板
```

## 爬虫说明

### 引擎

v3.0 起使用 **[crawl4ai](https://github.com/unclecode/crawl4ai)** 作为爬虫引擎（替代直接 Playwright 操作）：

- 自动处理浏览器生命周期、反爬机制
- 支持 `text_mode` 节省带宽（阻止图片/字体等资源）
- 输出干净的 Markdown / HTML 供后续解析

### 磁力提取

磁力提取逻辑独立于爬虫引擎，位于 `magnet_parser.py`，支持：

- **标准 magnet:** 格式
- **Base64 编码** 的磁力链接
- **JSON/属性中** 的磁力链接
- 自动去重（按 infohash）

## 测试

```bash
python tests/test_magnet_extract.py    # 磁力提取单元测试（无需网络）
python tests/test_imports.py           # 模块导入验证
python tests/test_crawler_adapter.py   # 爬虫生命周期测试
python tests/test_crawler_crawl.py     # 爬虫端到端测试（需网络）
```

## 技术栈

- **FastAPI** — Web 框架（异步 API + WebSocket）
- **crawl4ai** — 爬虫引擎（基于 Playwright）
- **MiniMax API** — Agent 对话（可选，磁力分类不需要）
- **Anthropic SDK** — MiniMax API（Claude 兼容接口，仅 Agent 使用）
- **qBittorrent Web API v2** — 下载管理
- **Pydantic v2** — 配置 + 数据模型

## License

MIT
