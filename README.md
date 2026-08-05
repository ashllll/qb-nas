# Magnet Harvester

通用磁力链接采集与分类服务。抓取网页中的 `magnet:` 链接，用**纯本地规则链**智能分类，并提交到 qBittorrent 下载到 NAS。附带 macOS 风格单页 Web UI、WebSocket 实时推送、系统剪贴板监控与 Agent 工具接口。

## 当前能力

- **Scrapling Spider 爬虫**：请求队列、并发、按域限流、深度跟进、指纹去重、重试与 `robots.txt` 均由 Scrapling 负责
- **纯本地分类规则链**：关键词规则（KeywordRule）→ 工作室/厂牌识别（StudioRule）→ 通用回退（FallbackRule），零外部 AI 依赖
- **分类结果 LRU 缓存**：命中/未命中统计与一键清理，规则重载自动失效
- **站点 Cookie 注入**：`SITE_COOKIES` 支持爬取需要登录的网站
- **系统剪贴板监控**：自动检测复制的 magnet 链接，分类后加入表格
- **qBittorrent Web API v2 客户端**：自动建分类、403 自动重登录、路径安全解析、`ensure_category` 防并发竞态
- **qB 配置热替换**：`QBitRuntime` 只依赖最窄替换目标，配置变更原子生效、失败自动回滚（无需重启）
- **WebSocket 实时推送**：事件总线（MessageBus）驱动，事件携带 `updated_at` 版本号，前端按版本丢弃延迟到达的旧事件
- **WebSocket API Key 认证**：`/ws` 握手与写接口同策略校验（`API_KEY` 为空时保持兼容）
- **单页 Web UI**：无构建步骤，实时进度、分类筛选、搜索、重分类与批量下载；qB 连接状态面板
- **生产链路验证脚本**：可选真实环境 smoke test（真实 qB 登录、真实站点抓取、可选提交轮询）

## 架构

```text
┌─────────────┐     ┌─────────────────┐     ┌──────────────────┐
│   Web UI    │────▶│    FastAPI      │────▶│  MagnetCrawler   │
│ static/     │◀────│   main.py       │◀────│  Scrapling Spider│
└─────────────┘     └───────┬─────────┘     └────────┬─────────┘
                            │                        │
                 ┌──────────┼────────────┬───────────┼──────────────┐
                 ▼          ▼            ▼           ▼              ▼
          ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌────────────┐
          │LocalClass│ │Magnet   │ │Clipboard │ │QBitSync │ │ MessageBus │
          │ifier 规则链│ │Parser   │ │Monitor   │ │Loop     │ │ + 事件总线  │
          └──────────┘ └─────────┘ └──────────┘ └────┬────┘ └─────┬──────┘
                                                     │            │
                                      ┌──────────────┘            │
                                      ▼                           ▼
                               ┌─────────────┐            ┌─────────────┐
                               │ QBittorrent │            │ WSBroadcaster│
                               │ Client      │            │ (/ws 推送)   │
                               └─────────────┘            └─────────────┘
```

所有组件由 `assembly.build_runtime()` 在 FastAPI lifespan 中统一装配为 `AppContext`（依赖容器），服务之间通过构造函数注入，无模块级全局可变状态。状态变更统一走 `MagnetItemTransitions`（发现/分类/下载三个生命周期域）并发布总线事件。

### 数据流

1. 用户提交 URL（UI/API）→ `MagnetCrawler.crawl()` 提取 magnet 链接
2. 新条目经 `DiscoveryTransitions.found()` 入库并发布 `MAGNET_FOUND` 事件
3. `HarvestPipeline._stream_classify()` 运行本地规则链分类
4. 用户触发下载 → `QBittorrentClient.add_magnet()`（自动分类与保存路径）
5. `QBitSyncLoop` 每 2s 轮询 qB → 同步 torrent 状态 → 终态变化时发事件
6. `WSBroadcaster` 订阅总线 → 推送所有事件到 WebSocket 客户端

### 适用磁力站

适合抓取公开页面中直接暴露 magnet 链接，或详情页可解析出 magnet 的站点，例如：

- BT4G / BTDig / Nyaa / Sukebei / Tokyo Toshokan
- The Pirate Bay 镜像站 / 1337x 镜像站 / RARBG 镜像索引站
- 磁力猫等 BT/Magnet 索引类站点

需要登录的站点可通过 `SITE_COOKIES` 注入 Cookie。请仅抓取你有权访问和下载的内容；实际可抓取范围取决于页面是否包含 `magnet:?xt=urn:btih:` 链接，以及目标站点的访问策略。

## 前端展示

![Magnet Harvester macOS 风格工作台](docs/frontend-dashboard.png)

Web UI 是无构建步骤的单页应用，直接由 FastAPI 提供：

- 左侧：采集任务、自动下载开关、任务统计和活动日志
- 中央：资源库表格、分类筛选、搜索、选择、重分类和批量下载
- 右侧：qBittorrent 连接设置（热替换保存）、API Key 会话输入、运行状态
- 顶部：WebSocket、qBittorrent、剪贴板监控状态
- 移动端：底部分段导航，在"采集 / 资源库 / 设置"之间切换

前端不依赖外部字体、CDN 或打包工具；页面结构、样式、API 传输、资源状态和界面控制分别位于 `static/` 下的 HTML、CSS 与 JavaScript 模块中。

## 快速开始

### 前置条件

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)（推荐，依赖锁定）或 `pip`
- 可访问的 qBittorrent Web UI（v4.1+，推荐 v5）
- Scrapling 浏览器（`uv run scrapling install`，自动安装 Chromium）

### 安装

```bash
git clone https://github.com/ashllll/qb-nas.git
cd qb-nas

uv sync --extra dev --locked
uv run scrapling install          # 安装 Scrapling 浏览器（等价于 playwright install chromium）

cp .env.example .env
# 编辑 .env 填入 qBittorrent 连接信息
```

### 配置

`.env` 常用配置项（完整模板见 `.env.example`）：

| 变量                              | 说明                                                                | 默认值                          |
| --------------------------------- | ------------------------------------------------------------------- | ------------------------------- |
| `QBIT_HOST`                       | qBittorrent Web UI 地址                                             | `http://qbittorrent.local:8080` |
| `QBIT_USERNAME`                   | qB 用户名                                                           | `your-username`                 |
| `QBIT_PASSWORD`                   | qB 密码                                                             | —                               |
| `QBIT_SYNC_INTERVAL`              | qB 状态轮询间隔（秒）                                               | `2.0`                           |
| `SERVICE_HOST`                    | 服务监听地址                                                        | `127.0.0.1`                     |
| `SERVICE_PORT`                    | 服务端口                                                            | `8899`                          |
| `API_KEY`                         | 写操作与 `/ws` 的 `X-API-Key`/查询参数                              | 空（仅 loopback）               |
| `ALLOW_INSECURE_WRITE_API`        | 允许非 loopback 无认证                                              | `false`                         |
| `SITE_COOKIES`                    | 站点 Cookie 注入                                                    | `{}`                            |
| `CRAWLER_TIMEOUT`                 | 抓取超时秒                                                          | `30`                            |
| `CRAWLER_MAX_DEPTH`               | 最大深度                                                            | `2`                             |
| `CRAWLER_CONCURRENCY`             | 并发数                                                              | `6`                             |
| `CRAWLER_MAX_DETAIL_LINKS`        | 单次深爬最多详情页数                                                | `200`                           |
| `CRAWLER_ALLOWED_RESOLUTIONS`     | 爬虫必须保留的清晰度关键词                                          | `2160p,4k`                      |
| `CRAWLER_WAIT_UNTIL`              | 页面等待阶段                                                        | `load`                          |
| `CRAWLER_DELAY_BEFORE_HTML`       | 取 HTML 前额外等待秒                                                | `1.0`                           |
| `CRAWLER_SCAN_FULL_PAGE`          | 抓取前滚动完整页面                                                  | `true`                          |
| `CRAWLER_MAX_SCROLL_STEPS`        | 最大滚动步数                                                        | `8`                             |
| `CRAWLER_PROCESS_IFRAMES`         | 合并 iframe 内容                                                    | `true`                          |
| `CRAWLER_FLATTEN_SHADOW_DOM`      | 展开 Shadow DOM                                                     | `true`                          |
| `CRAWLER_MAX_RETRIES`             | Scrapling 阻断/网络重试次数                                         | `1`                             |
| `CRAWLER_CHECK_ROBOTS_TXT`        | 遵守 robots.txt                                                     | `false`                         |
| `CRAWLER_ALLOW_FAKE_IP`           | 允许 mihomo/Clash fake-IP（198.18.0.0/15）作为爬取目标（SSRF 豁免） | `false`                         |
| `FS_BASE_PATH`                    | 本地可写目录（可选）                                                | 空                              |
| `MIN_DISK_SPACE_GB`               | 磁盘告警阈值                                                        | `10.0`                          |
| `LOG_LEVEL` / `LOG_FILE`          | 日志级别 / 文件输出（自动轮转）                                     | `INFO` / 控制台                 |
| `STORE_BACKEND` / `STORE_DB_PATH` | 存储后端（`memory`/`sqlite`）与数据库路径                           | `memory` / —                    |

#### 爬取需要登录的网站

```bash
# .env 中添加
SITE_COOKIES={"example.com": "uid=123; sid=abc; token=xyz"}
```

获取方式：浏览器登录目标网站 → F12 → Application → Cookies → 拼接为 `name=value; name2=value2` 格式。重启服务后自动注入到爬虫浏览器。

#### 剪贴板监控

点击顶部状态栏的 **"剪贴板监控"** pill 开关，开启后自动检测系统剪贴板中复制的 magnet 链接：

```text
复制磁力链接 → 自动提取 btih + dn= 名称 → 本地规则分类 → 加入表格 → 可一键下载
```

无需额外配置，监控仅检测 `magnet:?xt=urn:btih:` 开头的链接。

### 本地启动

```bash
python run.py
```

访问 http://localhost:8899

## 使用流程

1. 输入目标 URL，选择爬取深度（1-3）
2. 可选开启自动下载
3. 磁力实时出现在中央表格，按分类筛选
4. 选择条目点击"下载"发送到 qBittorrent
5. 右侧面板可修改 qB 连接并保存（热替换，无需重启）

## 部署流程

### 1. 准备运行环境

```bash
git clone https://github.com/ashllll/qb-nas.git
cd qb-nas

uv sync --extra dev --locked
uv run scrapling install
```

macOS / Linux 使用 `source .venv/bin/activate`，Windows PowerShell 使用：

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. 配置 `.env`

```bash
cp .env.example .env
```

至少配置：

```env
QBIT_HOST=http://你的-qb-host:8080
QBIT_USERNAME=你的用户名
QBIT_PASSWORD=你的密码
SERVICE_HOST=127.0.0.1
SERVICE_PORT=8899
```

也可以首次启动后在前端保存 qBittorrent 地址、用户名和密码；保存成功会写回本机 `.env`，服务重启后继续使用同一配置。密码不会回传到前端，配置面板只显示"已保存密码"，再次保存时密码留空即可保持不变。`.env` 已被 Git 忽略，提交代码前请确认不要手动强制加入它。

如果要让局域网其他设备访问，不要裸露无认证写接口。设置强随机 `API_KEY`：

```env
SERVICE_HOST=0.0.0.0
API_KEY=换成一串足够长的随机密钥
ALLOW_INSECURE_WRITE_API=false
```

前端右侧"访问安全 / API Key"输入同一密钥后，即可执行爬取、下载、清空、配置保存等写操作；WebSocket 订阅同样需要该密钥（作为查询参数传递，见 `docs/verification.md` 的已知权衡说明）。

### 3. 启动服务

开发或手动运行：

```bash
python run.py
```

后台运行示例：

```bash
nohup .venv/bin/python run.py > magnet-harvester.log 2>&1 &
```

访问：

```text
http://服务器地址:8899
```

### 4. systemd 部署示例（Linux NAS）

创建 `/etc/systemd/system/magnet-harvester.service`：

```ini
[Unit]
Description=Magnet Harvester
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/qb-nas
EnvironmentFile=/opt/qb-nas/.env
ExecStart=/opt/qb-nas/.venv/bin/python /opt/qb-nas/run.py
Restart=on-failure
RestartSec=5
User=nas
Group=nas

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now magnet-harvester
sudo systemctl status magnet-harvester
```

查看日志：

```bash
journalctl -u magnet-harvester -f
```

### 5. 更新部署

```bash
cd /opt/qb-nas
git pull --ff-only
uv sync --extra dev --locked
uv run scrapling install
sudo systemctl restart magnet-harvester
```

更新后建议打开 Web UI 确认顶部状态栏：

- WebSocket：已连接
- qB：在线
- 剪贴板：按需开启

## API

| 方法        | 路径                     | 说明                         |
| ----------- | ------------------------ | ---------------------------- |
| `POST`      | `/api/crawl`             | 发起爬取                     |
| `POST`      | `/api/download`          | 批量下载                     |
| `POST`      | `/api/reclassify`        | 重新分类                     |
| `GET`       | `/api/items`             | 列出条目（支持筛选/分页）    |
| `GET`       | `/api/items/search`      | 关键字搜索                   |
| `DELETE`    | `/api/items`             | 清空条目                     |
| `GET`       | `/api/categories`        | 分类列表                     |
| `GET`       | `/api/status`            | qB 状态 + 条目数             |
| `GET`       | `/api/stats`             | 运行统计                     |
| `GET`       | `/api/health`            | 健康检查                     |
| `GET`       | `/api/config`            | qB 连接配置                  |
| `PUT`       | `/api/config`            | 更新 qB 连接（热替换）       |
| `GET`       | `/api/errors`            | 错误列表                     |
| `POST`      | `/api/errors/clear`      | 清空已确认错误               |
| `GET`       | `/api/tasks/{task_id}`   | 后台任务状态                 |
| `POST`      | `/api/classifier/reload` | 重载分类关键词规则           |
| `GET`       | `/api/clipboard`         | 剪贴板监控状态               |
| `POST`      | `/api/clipboard/start`   | 开启剪贴板监控               |
| `POST`      | `/api/clipboard/stop`    | 关闭剪贴板监控               |
| `WebSocket` | `/ws`                    | 实时事件推送（API_KEY 认证） |

## 项目结构

```text
qb-nas/
├── run.py                          # 入口脚本
├── pyproject.toml                  # 项目元数据 + 依赖 + ruff/pytest 配置
├── requirements.txt                # 运行时依赖（pip 方式）
├── uv.lock                         # uv 锁定依赖
├── .env.example                    # 环境变量模板
├── config/
│   └── category_keywords.json      # 关键词分类规则
├── static/
│   ├── index.html                  # Web UI 页面结构
│   ├── styles.css                  # 页面样式
│   ├── api_client.js               # API 传输与鉴权
│   ├── item_state.js               # 资源状态（含 seenAt 事件版本表）
│   └── app.js                      # WebSocket 与界面控制
├── scripts/
│   └── smoke_production.py         # 可选真实环境 smoke 验证
├── docs/
│   ├── verification.md             # 验证层次与验收边界
│   ├── adr/                        # 架构决策记录
│   ├── agents/                     # Agent 工作流文档
│   └── specs/                      # 规格文档
├── magnet_harvester/               # 主 Python 包
│   ├── main.py                     # FastAPI 应用 + lifespan（唯一装配点）
│   ├── assembly.py                 # build_runtime() 统一装配
│   ├── config.py                   # Pydantic 配置 (Settings + 子配置)
│   ├── models.py                   # Pydantic 模型
│   ├── errors.py                   # ErrorHandler 结构化错误
│   ├── crawler.py                  # MagnetCrawler（Scrapling 事件适配）
│   ├── scrapling_spider.py         # Scrapling Spider 调度与浏览器安全策略
│   ├── dynamic_page.py             # 动态页面预处理（拦截遮罩/全页扫描/iframe/Shadow DOM）
│   ├── magnet_sources.py           # 磁力链接来源提取与详情页业务筛选
│   ├── magnet_parser.py            # magnet 正则/Base64 提取
│   ├── logger.py                   # 日志配置（级别/滚动文件）
│   ├── pipeline.py                 # HarvestPipeline（爬取→分类→下载）
│   ├── transitions.py              # 状态转换域（发现/分类/下载）
│   ├── store.py                    # ItemStore（内存/sqlite）
│   ├── bus.py                      # MessageBus 事件总线
│   ├── api/
│   │   ├── routes.py               # REST API
│   │   ├── websocket.py            # /ws 广播（含 API Key 握手认证）
│   │   └── pages.py                # 静态页面路由
│   ├── classifier/
│   │   ├── local_classifier.py     # 规则链 + LRU 缓存
│   │   ├── rule.py                 # ClassificationRule 协议与规则实现
│   │   ├── keyword_recognizer.py   # 关键词识别
│   │   ├── studio_recognizer.py    # 工作室/厂牌识别
│   │   └── fallback.py             # 通用回退规则
│   ├── qbit_client/
│   │   ├── client.py               # QBittorrentClient 门面
│   │   ├── _transport.py           # HTTP 传输/登录/重试
│   │   ├── mapper.py               # qB 状态 → TaskStatus 映射
│   │   ├── paths.py                # 保存路径推断与安全
│   │   ├── submitter.py            # MagnetSubmitter
│   │   ├── sync_state.py           # 增量同步状态
│   │   └── stats.py                # QBittorrentStats
│   ├── services/
│   │   ├── qbit_sync.py            # QBitSyncLoop
│   │   ├── clipboard_monitor.py    # 剪贴板监控 (pyperclip)
│   │   ├── site_auth.py            # 站点 Cookie 注入
│   │   ├── observability.py        # 状态/健康/统计快照
│   │   ├── user_actions.py         # 用户动作执行器
│   │   ├── item_queries.py         # 只读条目查询
│   │   └── stats.py                # SystemStats
│   ├── context/
│   │   └── app_context.py          # AppContext + QBitRuntime + QBitReplacementTarget
│   └── utils/
│       ├── auth.py                 # API Key 认证依赖
│       ├── url_validator.py        # SSRF 防护 + URL 验证
│       ├── serializers.py          # 响应序列化
│       └── bg_tasks.py             # BGTaskManager
└── tests/                          # 80+ 测试文件（pytest + pytest-asyncio）
```

## 核心实现

- **分类规则链**：`KeywordRule`（高置信关键词）→ `StudioRule`（工作室/厂牌识别）→ `FallbackRule`（正则回退，恒有结果）；类别：电影、电视剧、动漫、音乐、游戏、软件、综艺、纪录片、其他
- **分类缓存**：`LocalClassificationEngine` 内置线程安全 LRU 缓存（上限 1024），`get_cache_stats()`/`clear_cache()` 真实统计，`reload_rules()` 自动失效
- **事件版本化**：所有事件 payload 携带 `updated_at`（naive-local ISO 字符串，字典序 == 时间序）；前端 `item_state.js` 的 `seenAt` 版本表丢弃延迟到达的旧事件，防止旧事件覆盖新状态
- **爬虫调度**：Scrapling `Spider.stream()` 负责请求队列、并发、深度跟进、指纹去重、重试与 robots.txt，结果流式回传 WebSocket
- **浏览器网络防护**：Scrapling `page_setup` 在导航前拦截 HTTP/WebSocket 请求，阻止私网、非全局地址和 Service Worker 绕过
- **qB 客户端**：Cookie SID 认证 + 403 自动重登录 + 重试机制；`ensure_category` 带锁防并发竞态；路径解析防目录穿越
- **qB 热替换**：`QBitRuntime` 只依赖 `QBitReplacementTarget`（最窄依赖集），替换成功/失败均原子回写，配置持久化到 `.env`
- **状态同步**：`QBitSyncLoop` 每 2s 轮询 `/sync/maindata`，仅状态变化时发事件
- **URL 安全**：RFC 1918/链路本地/组播精确检查 + DNS 解析后验证，防 SSRF；`CRAWLER_ALLOW_FAKE_IP` 可豁免 mihomo/Clash fake-IP（默认关闭）
- **剪贴板监控**：`pyperclip` 轮询（1s），提取 `btih` + `dn=` 名称，分类后发布 `MAGNET_FOUND` 事件

## 验证

```bash
python -m pytest tests -q        # 全量测试（80+ 文件，fake qB/模拟爬虫）
.venv/bin/ruff check magnet_harvester tests   # 静态检查
```

- **自动化测试 ≠ 生产验收**：真实站点抓取、真实 qB 登录、NAS 下载链路请使用 `scripts/smoke_production.py`（可选，见 `docs/verification.md`）
- WebSocket 事件版本不变量：不要引入 aware-UTC 时间戳或混合时区，否则前端 `seenAt` 比较会失序

## License

MIT
