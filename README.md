# Magnet Harvester

通用磁力链接采集与分类服务。抓取网页中的 magnet 链接，用本地规则智能分类，并将任务发送到 qBittorrent 下载到 NAS。

## 当前能力

- 基于 `crawl4ai` (Playwright) 抓取页面和子链接中的 magnet
- **三层分类引擎**：工作室识别 → 关键词匹配 → 通用规则（47 条）
- 支持站点 Cookie 注入，爬取需要登录的网站
- **系统剪贴板监控**：自动检测复制到的 magnet 链接，分类后加入表格
- 通过 qBittorrent Web API v2 自动建分类并添加下载
- 单页 Web UI，实时进度、筛选、重分类和批量下载
- qB 连接状态面板（在线/离线/检测中 + 状态指示灯）

## 架构

```text
┌─────────────┐     ┌─────────────┐     ┌────────────────┐
│   Web UI    │────▶│  FastAPI    │────▶│  MagnetCrawler │
│ index.html  │◀────│  main.py    │◀────│   crawl4ai     │
└─────────────┘     └──────┬──────┘     └───────┬────────┘
                           │                    │
        ┌──────────────────┼────────────────────┼──────────────┐
        ▼                  ▼                    ▼              ▼
   ┌───────────┐    ┌──────────────┐    ┌────────────┐  ┌──────────┐
   │ Classifier│    │ SiteAuth     │    │MagnetParser│  │ qBittorrent
   │ 3-layer   │    │ Cookie注入    │    │ regex      │  │ Client  │
   └───────────┘    └──────────────┘    └────────────┘  └──────────┘
                           ▲
                    ┌──────────────┐
                    │ClipboardMon  │
                    │ pyperclip轮询 │
                    └──────────────┘
```

### 分类引擎

```
输入标题
  ├─ 1. 关键词  (keyword_recognizer) → "ubuntu" → 软件
  ├─ 2. 工作室  (studio_recognizer)  → "SexArt 26 05 20..." → SexArt
  └─ 3. 通用规则 (fallback.py 47条) → "BluRay.2024..." → 电影
```

支持 36 个已知工作室自动映射，覆盖电影/剧集/动漫/音乐/游戏/软件/综艺/纪录片八大类别。

## 快速开始

### 前置条件

- Python 3.11+
- 可访问的 qBittorrent Web UI (v4.1+)
- Playwright Chromium (`playwright install chromium`)

### 安装

```bash
git clone https://github.com/ashllll/qb-nas.git
cd qb-nas

python -m pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# 编辑 .env 填入 qBittorrent 连接信息
```

### 配置

`.env` 常用配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `QBIT_HOST` | qBittorrent Web UI 地址 | `http://192.168.1.100:8080` |
| `QBIT_USERNAME` | qB 用户名 | `admin` |
| `QBIT_PASSWORD` | qB 密码 | — |
| `SERVICE_HOST` | 服务监听地址 | `127.0.0.1` |
| `SERVICE_PORT` | 服务端口 | `8899` |
| `API_KEY` | 写操作 `X-API-Key` | 空（仅 loopback） |
| `ALLOW_INSECURE_WRITE_API` | 允许非 loopback 无认证 | `false` |
| `SITE_COOKIES` | 站点 Cookie 注入 | `{}` |
| `CRAWLER_TIMEOUT` | 抓取超时秒 | `30` |
| `CRAWLER_MAX_DEPTH` | 最大深度 | `2` |
| `CRAWLER_CONCURRENCY` | 并发数 | `3` |
| `FS_BASE_PATH` | 本地可写目录（可选） | 空 |
| `MIN_DISK_SPACE_GB` | 磁盘告警阈值 | `10.0` |

#### 爬取需要登录的网站

```bash
# .env 中添加
SITE_COOKIES={"example.com": "uid=123; sid=abc; token=xyz"}
```

获取方式：浏览器登录目标网站 → F12 → Application → Cookies → 拼接为 `name=value; name2=value2` 格式。重启服务后自动注入到爬虫浏览器。

#### 剪贴板监控

点击顶部状态栏的 **"剪贴板监控"** pill 开关，开启后自动检测系统剪贴板中复制的 magnet 链接：

```
复制磁力链接 → 自动提取 btih + dn= 名称 → 三层分类 → 加入表格 → 可一键下载
```

无需额外配置，监控仅检测 `magnet:?xt=urn:btih:` 开头的链接。

### 启动

```bash
python run.py
```

访问 http://localhost:8899

## Web UI

1. 输入目标 URL，选择爬取深度（1-3）
2. 可选开启自动下载
3. 磁力实时出现在中央表格，按分类筛选
4. 选择条目点击"下载"发送到 qBittorrent
5. 右侧面板可修改 qB 连接并测试

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/crawl` | 发起爬取 |
| `POST` | `/api/download` | 批量下载 |
| `POST` | `/api/reclassify` | 重新分类 |
| `GET` | `/api/items` | 列出条目（支持筛选/分页） |
| `GET` | `/api/items/search` | 关键字搜索 |
| `DELETE` | `/api/items` | 清空条目 |
| `GET` | `/api/categories` | 分类列表 |
| `GET` | `/api/status` | qB 状态 + 条目数 |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/config` | qB 连接配置 |
| `PUT` | `/api/config` | 更新 qB 连接 |
| `GET` | `/api/errors` | 错误列表 |
| `GET` | `/api/clipboard` | 剪贴板监控状态 |
| `POST` | `/api/clipboard/start` | 开启剪贴板监控 |
| `POST` | `/api/clipboard/stop` | 关闭剪贴板监控 |
| `WebSocket` | `/ws` | 实时事件推送 |

## 项目结构

```text
qb-nas/
├── run.py                          # 入口脚本
├── magnet_harvester/
│   ├── main.py                     # FastAPI 应用 + lifespan
│   ├── config.py                   # Pydantic 配置 (Settings)
│   ├── models.py                   # Pydantic 模型
│   ├── errors.py                   # 错误处理 (ErrorHandler)
│   ├── crawler.py                  # crawl4ai 爬虫 + Cookie 注入
│   ├── magnet_parser.py            # magnet 正则提取
│   ├── pipeline.py                 # 爬取→分类→下载管道
│   ├── store.py                    # ItemStore (内存存储)
│   ├── bus.py                      # MessageBus (事件总线)
│   ├── assembly.py                 # 运行时装配 (build_runtime)
│   ├── api/
│   │   ├── routes.py               # REST API
│   │   ├── websocket.py            # WebSocket 广播
│   │   └── pages.py                # 静态页面路由
│   ├── classifier/
│   │   ├── local_classifier.py     # 主分类器 (3层)
│   │   ├── studio_recognizer.py    # 工作室/厂牌识别 (36个)
│   │   ├── keyword_recognizer.py   # 关键词匹配
│   │   └── fallback.py             # 通用规则 (47条)
│   ├── qbit_client/
│   │   ├── client.py               # qB API v2 客户端
│   │   ├── paths.py                # 路径解析 + 安全处理
│   │   └── __init__.py
│   ├── services/
│   │   ├── qbit_sync.py            # qB 状态同步循环
│   │   ├── site_auth.py            # 站点 Cookie 注入
│   │   ├── clipboard_monitor.py    # 剪贴板监控 (pyperclip)
│   │   ├── stats.py                # 运行时统计
│   │   └── __init__.py
│   ├── context/
│   │   └── app_context.py          # AppContext + 依赖注入
│   └── utils/
│       ├── auth.py                 # API Key 认证
│       ├── url_validator.py        # SSRF 防护 + URL 验证
│       ├── serializers.py          # 响应序列化
│       └── bg_tasks.py             # 后台任务管理
├── static/
│   └── index.html                  # Web UI 单页应用
├── config/
│   └── category_keywords.json      # 关键词规则配置
├── tests/                          # 单元测试
├── .env.example                    # 环境变量模板
└── requirements.txt
```

## 核心实现

- **分类引擎**：三层次：关键词精确匹配 > 工作室自动提取 > 47 条通用正则。工作室识别支持 36 个已知厂牌 + 未知厂牌自动发现，兼容空格/点/横线分隔的日期格式
- **Cookie 注入**：`SITE_COOKIES` JSON 配置 → `BrowserConfig.cookies` → crawl4ai 浏览器自动携带，支持多域名
- **qB 客户端**：Cookie SID 认证 + 403 自动重登录 + 重试机制。`ensure_category` 带锁防并发竞态，`use_auto_torrent_management` 自动路由
- **状态同步**：QBitSyncLoop 每 2 秒轮询 `/sync/maindata`，仅终态变化时触发前端通知，避免日志刷屏
- **URL 安全**：RFC 1918 精确检查（10/172.16/192.168 + fc00::/7），DNS 解析后验证，防 SSRF
- **剪贴板监控**：`pyperclip` 轮询系统剪贴板 (1s)，提取 `btih` + `dn=` 名称，经过三层分类引擎后发布 `MAGNET_FOUND` 事件，实时显示在 Web UI 表格中

## License

MIT
