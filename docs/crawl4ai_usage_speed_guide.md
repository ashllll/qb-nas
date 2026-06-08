# Crawl4AI 使用方法与高速爬取实践指南

> 项目：<https://github.com/unclecode/crawl4ai>  
> 文档版本：基于 Crawl4AI 官方仓库 README 与官方文档 v0.8.x 二次核对整理  
> 生成时间：2026-06-08  
> 目标：快速掌握 Crawl4AI 的正确使用方式，并尽量发挥其异步、浏览器复用、并发调度、缓存、内容过滤、结构化抽取、Docker 服务化等优势。

---

## 1. 项目定位

Crawl4AI 是一个面向 **LLM / RAG / AI Agent / 数据管道** 的开源网页爬取与抽取框架。它的核心优势不是单纯“下载 HTML”，而是把网页转成更适合 AI 使用的：

- 干净 Markdown；
- 结构化 JSON；
- 可过滤的正文内容；
- 可控的浏览器行为；
- 可并发的大批量抓取结果；
- 可扩展的 Docker 服务接口。

官方 README 对它的定位是：将网页转成 clean、LLM-ready Markdown，适用于 RAG、Agents 和数据管道。

---

## 2. 二次核对结论

以下内容已按官方仓库与官方文档重新核对：

| 项目 | 核对结论 |
|---|---|
| 安装命令 | 官方 README 使用 `pip install -U crawl4ai`，然后运行 `crawl4ai-setup` 和 `crawl4ai-doctor`。 |
| 浏览器依赖 | Crawl4AI 默认使用 Playwright / Chromium；如安装后浏览器异常，可手动执行 Playwright install。 |
| 核心类 | 官方文档推荐 `AsyncWebCrawler` + `BrowserConfig` + `CrawlerRunConfig`。 |
| 缓存 | v0.5+ 使用 `CacheMode` 枚举替代旧的 `bypass_cache` / `disable_cache` 等布尔参数。 |
| 并发 | 多 URL 推荐 `arun_many()`；需要更强控制时使用 Dispatcher。 |
| 默认并发调度 | 官方多 URL 文档说明 `arun_many()` 内置 Dispatcher，可用 `MemoryAdaptiveDispatcher` 做内存自适应并发控制。 |
| 高速爬取 | 官方支持 `text_mode`、`light_mode`、`avoid_ads`、`avoid_css`、`stream=True`、缓存、Dispatcher、内容过滤等提速手段。 |
| Docker | 官方 README 提供 `unclecode/crawl4ai:latest` 镜像，默认服务端口是 `11235`。 |
| 安全提醒 | 官方 v0.8.7+ / v0.8.8 / v0.8.9 主要包含 Docker API 安全修复；自托管 Docker API 建议及时升级。 |

---

## 3. 安装

### 3.1 Windows 推荐安装方式

```powershell
mkdir crawl4ai-demo
cd crawl4ai-demo

python -m venv .venv
.\.venv\Scripts\activate

python -m pip install -U pip
pip install -U crawl4ai

crawl4ai-setup
crawl4ai-doctor
```

如果 Playwright 浏览器依赖异常，手动安装 Chromium：

```powershell
python -m playwright install chromium
```

如果是 Linux / Docker 环境，可以使用：

```bash
python -m playwright install --with-deps chromium
```

---

## 4. 最小可用示例

```python
import asyncio
from crawl4ai import AsyncWebCrawler

async def main():
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun("https://example.com")
        if result.success:
            print(result.markdown[:500])
        else:
            print("ERROR:", result.error_message)

if __name__ == "__main__":
    asyncio.run(main())
```

这个示例会启动一个无头浏览器，访问网页，并输出自动转换后的 Markdown。

---

## 5. 核心概念

### 5.1 `AsyncWebCrawler`

核心异步爬虫对象。推荐做法是：

- 一个批次任务里只创建一次 crawler；
- 在 `async with AsyncWebCrawler(...) as crawler:` 中复用；
- 不要每个 URL 都重新创建一个 crawler，否则浏览器启动开销会很大。

### 5.2 `BrowserConfig`

控制浏览器层面的配置，例如：

- 是否 headless；
- 浏览器类型；
- User-Agent；
- 代理；
- 持久化用户目录；
- 是否 text mode；
- 是否 light mode；
- 是否阻止广告、CSS 等。

### 5.3 `CrawlerRunConfig`

控制每次爬取行为，例如：

- 缓存模式；
- CSS 选择器；
- 排除标签；
- JS 执行；
- 等待条件；
- 超时时间；
- Markdown 生成器；
- 结构化抽取策略；
- `stream=True`；
- 深度爬取策略。

### 5.4 `CacheMode`

新版本推荐使用 `CacheMode`：

| 模式 | 说明 | 常用场景 |
|---|---|---|
| `CacheMode.ENABLED` | 正常读写缓存 | 重复爬取、调试、RAG 文档归档 |
| `CacheMode.BYPASS` | 跳过读取缓存，重新抓取 | 新闻、价格、动态数据 |
| `CacheMode.DISABLED` | 不读也不写 | 一次性任务、极简环境 |
| `CacheMode.READ_ONLY` | 只读缓存 | 离线重处理 |
| `CacheMode.WRITE_ONLY` | 只写缓存，不读旧缓存 | 首次批量建库 |

建议：**不要依赖默认值，显式写 `cache_mode`**。官方不同页面对默认值表述存在差异，实际项目中显式配置最稳。

---

## 6. 推荐项目结构

```text
crawl4ai_project/
├─ input/
│  └─ urls.txt
├─ output/
│  ├─ markdown/
│  ├─ json/
│  └─ logs/
├─ fast_batch_crawl.py
├─ requirements.txt
└─ README.md
```

`requirements.txt`：

```text
crawl4ai>=0.8
```

---

## 7. 高速批量爬取模板

下面这个模板适合批量抓取文档站、资讯页、博客页，并尽量提高速度。

主要优化点：

- 使用 `arun_many()` 批量并发；
- 使用 `MemoryAdaptiveDispatcher` 控制并发和内存；
- 使用 `stream=True`，结果完成一个处理一个；
- 只创建一个 `AsyncWebCrawler`；
- 使用 `text_mode=True`、`light_mode=True`、`avoid_ads=True`、`avoid_css=True`；
- 不截图、不生成 PDF；
- 排除 `nav`、`footer`、`form` 等无效区域；
- 按需开启缓存。

```python
import asyncio
from pathlib import Path
from urllib.parse import urlparse

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    RateLimiter,
    CrawlerMonitor,
    DisplayMode,
)
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher

OUTPUT_DIR = Path("output/markdown")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def safe_filename(url: str) -> str:
    parsed = urlparse(url)
    name = f"{parsed.netloc}{parsed.path}".strip("/") or parsed.netloc
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:180] + ".md"


async def main():
    urls = [
        "https://docs.crawl4ai.com/core/quickstart/",
        "https://docs.crawl4ai.com/core/browser-crawler-config/",
        "https://docs.crawl4ai.com/advanced/multi-url-crawling/",
    ]

    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        verbose=False,

        # 文本爬取提速配置
        text_mode=True,      # 尽量禁用图片和重资源
        light_mode=True,     # 关闭部分后台能力，提升性能
        avoid_ads=True,      # 阻止常见广告 / 追踪资源
        avoid_css=True,      # 只要正文时可关闭 CSS
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,  # 重复爬取建议 ENABLED；实时数据改 BYPASS
        stream=True,
        wait_until="domcontentloaded",
        page_timeout=30000,
        delay_before_return_html=0.05,

        # 内容过滤：减少 Markdown 噪音，也降低后处理成本
        excluded_tags=["nav", "footer", "aside", "form", "script", "style"],
        exclude_external_links=True,
        exclude_social_media_links=True,
        remove_forms=True,
        remove_overlay_elements=True,

        # 不需要视觉结果时不要开
        screenshot=False,
        pdf=False,
        verbose=False,
    )

    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=75.0,
        check_interval=1.0,
        max_session_permit=8,
        rate_limiter=RateLimiter(
            base_delay=(0.2, 0.8),
            max_delay=10.0,
            max_retries=2,
            rate_limit_codes=[429, 503],
        ),
        monitor=CrawlerMonitor(
            max_visible_rows=12,
            display_mode=DisplayMode.AGGREGATED,
        ),
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        async for result in await crawler.arun_many(
            urls=urls,
            config=run_config,
            dispatcher=dispatcher,
        ):
            if result.success:
                md = result.markdown.raw_markdown
                path = OUTPUT_DIR / safe_filename(result.url)
                path.write_text(md, encoding="utf-8")
                print(f"[OK] {result.url} -> {path}")
            else:
                print(f"[FAIL] {result.url}: {result.error_message}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 8. 速度优化策略总表

| 优化项 | 推荐配置 | 适合场景 | 注意事项 |
|---|---|---|---|
| 浏览器复用 | 一个 `AsyncWebCrawler` 处理一批 URL | 所有批量任务 | 不要每个 URL 新建 crawler |
| 并发爬取 | `arun_many()` | 多 URL | 比循环 `arun()` 更高效 |
| 内存自适应 | `MemoryAdaptiveDispatcher` | 中大型任务 | 根据机器内存调 `max_session_permit` |
| 流式处理 | `stream=True` | 大批量 URL | 可边爬边写文件，降低内存峰值 |
| 缓存 | `CacheMode.ENABLED` | 文档站、重复调试 | 新闻/价格类用 `BYPASS` |
| 文本模式 | `text_mode=True` | 只要文本 | 不适合需要图片信息的任务 |
| 轻量模式 | `light_mode=True` | 高速正文抽取 | 某些复杂页面可能需要关闭 |
| 阻止广告 | `avoid_ads=True` | 内容站、资讯站 | 通常建议开启 |
| 阻止 CSS | `avoid_css=True` | 只要正文 | 页面依赖 CSS 选择器渲染时慎用 |
| 跳过截图 | `screenshot=False` | 文本 / JSON 抽取 | 截图会显著增加耗时和体积 |
| 跳过 PDF | `pdf=False` | 文本 / JSON 抽取 | PDF 生成很耗资源 |
| 早结束等待 | `wait_until="domcontentloaded"` | 静态 / 半静态页面 | SPA 可能需要 `wait_for` |
| CSS 范围选择 | `css_selector="main"` | 文档站、博客 | 需要先确认页面结构 |
| 排除无关标签 | `excluded_tags=[...]` | 减少噪音 | 不要误删正文标签 |
| CSS JSON 抽取 | `JsonCssExtractionStrategy` | 结构稳定页面 | 比 LLM 抽取快、便宜 |
| LLM 抽取 | `LLMExtractionStrategy` | 结构复杂页面 | 成本高、速度慢，慎用于全量 |
| URL 预筛选 | sitemap / URL seeding / 规则过滤 | 大站 | 先过滤再爬，比盲目深爬快 |
| 深度爬取限制 | `max_depth <= 2~3`、`max_pages` | 站内探索 | 深度过大 URL 数会指数增长 |

---

## 9. 内容抽取方式选择

### 9.1 只要 Markdown

适合：RAG 知识库、网页归档、AI 输入上下文。

```python
from crawl4ai import CrawlerRunConfig, CacheMode

config = CrawlerRunConfig(
    cache_mode=CacheMode.ENABLED,
    excluded_tags=["nav", "footer", "aside", "script", "style"],
    exclude_external_links=True,
)
```

读取结果：

```python
md = result.markdown.raw_markdown
```

如果用了内容过滤器，也可以读取：

```python
fit_md = result.markdown.fit_markdown
```

### 9.2 结构化 JSON：优先 CSS / XPath，不要一上来用 LLM

适合：列表页、商品页、文章索引页、表格结构稳定的网站。

```python
import asyncio
import json
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode, JsonCssExtractionStrategy

schema = {
    "name": "Articles",
    "baseSelector": "article",
    "fields": [
        {"name": "title", "selector": "h2", "type": "text"},
        {"name": "link", "selector": "a", "type": "attribute", "attribute": "href"},
        {"name": "summary", "selector": "p", "type": "text"},
    ],
}

async def main():
    config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        extraction_strategy=JsonCssExtractionStrategy(schema),
        css_selector="main",
    )

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun("https://example.com/blog", config=config)
        if result.success:
            data = json.loads(result.extracted_content)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(result.error_message)

asyncio.run(main())
```

### 9.3 LLM 抽取：只用于复杂页面或一次性生成 schema

适合：页面结构不稳定、字段语义复杂、普通选择器难以覆盖的情况。

注意：

- LLM 抽取成本和耗时都更高；
- 大批量任务不建议每页都用 LLM；
- 推荐先用 LLM 生成 CSS schema，再用 `JsonCssExtractionStrategy` 做高速重复抽取。

---

## 10. 动态页面处理

如果页面需要点击“加载更多”、滚动或等待 JS 渲染，可以使用：

- `js_code`：注入并执行 JS；
- `wait_for`：等待 CSS 或 JS 条件满足；
- `scan_full_page=True`：滚动页面触发懒加载；
- `delay_before_return_html`：最终取 HTML 前短暂停顿。

示例：点击加载更多后再抽取。

```python
from crawl4ai import CrawlerRunConfig, CacheMode

config = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    js_code="document.querySelector('.load-more')?.click();",
    wait_for="css:.item",
    page_timeout=60000,
)
```

性能建议：

- 能不用 `networkidle` 就不用，优先 `domcontentloaded` + 精准 `wait_for`；
- 不要无脑 `delay_before_return_html=5`，通常 `0.05~0.5` 秒足够；
- 对需要登录态的页面，使用持久化浏览器上下文。

---

## 11. 会话、登录态与持久化浏览器

对于需要登录的页面，推荐使用持久化上下文：

```python
from pathlib import Path
from crawl4ai import BrowserConfig

browser_config = BrowserConfig(
    headless=True,
    use_persistent_context=True,
    user_data_dir=str(Path.home() / ".crawl4ai" / "browser_profile"),
)
```

典型用途：

- 保留 cookies；
- 保留 localStorage；
- 复用已登录状态；
- 避免每次重新登录。

调试时可以先设置：

```python
BrowserConfig(headless=False, verbose=True)
```

登录完成后再切回 headless。

---

## 12. 深度爬取策略

Crawl4AI 支持站内多层级爬取。适合未知站点结构、需要自动发现链接的场景。

### 12.1 BFS 示例

```python
import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy

async def main():
    config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=2,
            include_external=False,
            max_pages=50,
        ),
        scraping_strategy=LXMLWebScrapingStrategy(),
        stream=True,
        cache_mode=CacheMode.ENABLED,
    )

    async with AsyncWebCrawler() as crawler:
        async for result in await crawler.arun("https://docs.crawl4ai.com", config=config):
            if result.success:
                depth = result.metadata.get("depth", 0)
                print(f"depth={depth} {result.url}")
            else:
                print("FAIL", result.url, result.error_message)

asyncio.run(main())
```

### 12.2 深度爬取提速原则

- `max_depth` 不要轻易超过 3；
- 一定设置 `max_pages`；
- `include_external=False`，除非确实需要跨站；
- 用 URL pattern / domain filter 先过滤；
- 对目标明确的任务，优先 Best-First / 关键词评分；
- 用 `stream=True` 边爬边处理。

---

## 13. URL Seeding：大站优先用“先找 URL，再爬正文”

对于大型文档站、博客站、电商站，不建议直接深度爬取全站。更快策略是：

```text
sitemap / URL seeding / 规则发现 URL
        ↓
按路径、关键词、内容类型过滤
        ↓
只把高价值 URL 交给 arun_many()
        ↓
并发爬取正文
```

适合：

- 文档站归档；
- RAG 知识库构建；
- 竞品页面采集；
- 大批量文章抓取；
- 只要特定栏目，不要全站。

---

## 14. Docker 服务化部署

官方 Docker 快速启动：

```bash
docker pull unclecode/crawl4ai:latest
docker run -d \
  -p 11235:11235 \
  --name crawl4ai \
  --shm-size=1g \
  unclecode/crawl4ai:latest
```

访问：

```text
http://localhost:11235/dashboard
http://localhost:11235/playground
```

快速调用：

```python
import requests

response = requests.post(
    "http://localhost:11235/crawl",
    json={
        "urls": ["https://example.com"],
        "priority": 10,
    },
    timeout=60,
)

print(response.status_code)
print(response.json())
```

Docker 适合：

- 多项目共享一个爬虫服务；
- 让非 Python 项目通过 HTTP 调用；
- 统一管理浏览器池；
- 需要 Dashboard 观察任务状态；
- 后续接入任务队列。

安全建议：

- 不要裸露到公网；
- 用反向代理、鉴权、内网访问；
- 及时升级 v0.8.9+，官方近期多次修复 Docker API 安全问题；
- 如果需要公网访问，必须加认证、HTTPS、IP 白名单。

---

## 15. 生产级高速爬取脚本

下面脚本更接近实际项目可用版本：

- 从 `input/urls.txt` 读取 URL；
- 并发爬取；
- 成功结果保存 Markdown；
- 失败结果保存日志；
- 支持缓存；
- 支持内存自适应并发；
- 支持 Windows 路径。

```python
import asyncio
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    RateLimiter,
    CrawlerMonitor,
    DisplayMode,
)
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "input" / "urls.txt"
MD_DIR = BASE_DIR / "output" / "markdown"
LOG_DIR = BASE_DIR / "output" / "logs"

MD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_urls() -> list[str]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"找不到 URL 文件：{INPUT_FILE}")

    urls = []
    for line in INPUT_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def safe_filename(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}".strip("/") or parsed.netloc
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw)
    return safe[:180] + ".md"


async def main():
    urls = load_urls()
    print(f"待爬取 URL 数量：{len(urls)}")

    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        verbose=False,
        text_mode=True,
        light_mode=True,
        avoid_ads=True,
        avoid_css=True,
        user_agent_mode="random",
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        stream=True,
        check_robots_txt=True,
        wait_until="domcontentloaded",
        page_timeout=30000,
        delay_before_return_html=0.05,
        excluded_tags=["nav", "footer", "aside", "script", "style", "form"],
        exclude_external_links=True,
        exclude_social_media_links=True,
        remove_forms=True,
        remove_overlay_elements=True,
        screenshot=False,
        pdf=False,
        verbose=False,
    )

    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=75.0,
        check_interval=1.0,
        max_session_permit=8,
        rate_limiter=RateLimiter(
            base_delay=(0.3, 1.0),
            max_delay=20.0,
            max_retries=2,
            rate_limit_codes=[429, 503],
        ),
        monitor=CrawlerMonitor(
            max_visible_rows=20,
            display_mode=DisplayMode.AGGREGATED,
        ),
    )

    failures = []
    success_count = 0

    async with AsyncWebCrawler(config=browser_config) as crawler:
        async for result in await crawler.arun_many(
            urls=urls,
            config=run_config,
            dispatcher=dispatcher,
        ):
            if result.success:
                md = result.markdown.raw_markdown
                output_path = MD_DIR / safe_filename(result.url)
                output_path.write_text(md, encoding="utf-8")
                success_count += 1
                print(f"[OK] {result.url} -> {output_path.name}")
            else:
                failures.append({
                    "url": result.url,
                    "status_code": getattr(result, "status_code", None),
                    "error": result.error_message,
                })
                print(f"[FAIL] {result.url}: {result.error_message}")

    log_path = LOG_DIR / f"crawl_failures_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    log_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"成功：{success_count}")
    print(f"失败：{len(failures)}")
    print(f"失败日志：{log_path}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 16. `input/urls.txt` 示例

```text
https://docs.crawl4ai.com/core/quickstart/
https://docs.crawl4ai.com/core/browser-crawler-config/
https://docs.crawl4ai.com/advanced/multi-url-crawling/
https://docs.crawl4ai.com/core/cache-modes/
```

---

## 17. 针对不同目标的推荐配置

### 17.1 文档站 / 博客站归档

```python
BrowserConfig(
    headless=True,
    text_mode=True,
    light_mode=True,
    avoid_ads=True,
    avoid_css=True,
)

CrawlerRunConfig(
    cache_mode=CacheMode.ENABLED,
    stream=True,
    css_selector="main",
    excluded_tags=["nav", "footer", "aside", "script", "style"],
    exclude_external_links=True,
)
```

### 17.2 新闻 / 价格 / 高频变化页面

```python
CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    wait_until="domcontentloaded",
    page_timeout=30000,
)
```

### 17.3 SPA / 动态加载页面

```python
CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    wait_for="css:.content-loaded",
    js_code="window.scrollTo(0, document.body.scrollHeight);",
    page_timeout=60000,
)
```

### 17.4 结构稳定的列表页

```python
CrawlerRunConfig(
    cache_mode=CacheMode.ENABLED,
    extraction_strategy=JsonCssExtractionStrategy(schema),
    css_selector="main",
)
```

### 17.5 反爬较强页面

```python
BrowserConfig(
    headless=True,
    user_agent_mode="random",
    enable_stealth=True,
)

CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    simulate_user=True,
    magic=True,
    max_retries=2,
)
```

注意：反爬相关配置必须遵守目标网站条款、robots.txt 和当地法律法规。

---

## 18. 常见坑

### 18.1 每个 URL 都创建一个 crawler

不推荐：

```python
for url in urls:
    async with AsyncWebCrawler() as crawler:
        await crawler.arun(url)
```

推荐：

```python
async with AsyncWebCrawler(config=browser_config) as crawler:
    results = await crawler.arun_many(urls, config=run_config)
```

### 18.2 没有显式设置缓存

不要依赖默认值：

```python
CrawlerRunConfig(cache_mode=CacheMode.ENABLED)
```

或：

```python
CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
```

### 18.3 无脑等待 `networkidle`

很多站点会持续请求广告、统计、长连接，`networkidle` 可能让任务变慢。建议优先：

```python
wait_until="domcontentloaded"
wait_for="css:.main-content"
```

### 18.4 不设置深度限制

深度爬取时必须设置：

```python
max_depth=2
max_pages=50
include_external=False
```

### 18.5 开启截图 / PDF 导致很慢

不需要视觉结果时保持：

```python
screenshot=False
pdf=False
```

### 18.6 直接用 LLM 抽取全站

LLM 抽取适合复杂页面，不适合无脑全量。推荐顺序：

```text
CSS / XPath schema
    ↓
内容过滤 + Markdown
    ↓
只对少量复杂页面使用 LLM
```

---

## 19. 推荐调参顺序

如果你觉得速度不够，按下面顺序调：

1. 确认是否使用 `arun_many()`；
2. 确认是否只创建一个 `AsyncWebCrawler`；
3. 开启 `stream=True`；
4. 设置 `text_mode=True`、`light_mode=True`；
5. 开启 `avoid_ads=True`、`avoid_css=True`；
6. 关闭 `screenshot` 和 `pdf`；
7. 使用 `css_selector` 限定正文区域；
8. 使用 `excluded_tags` 删除导航、页脚、表单；
9. 对重复任务开启 `CacheMode.ENABLED`；
10. 调整 `MemoryAdaptiveDispatcher(max_session_permit=...)`；
11. 对 429 / 503 增加 `RateLimiter`；
12. 大站先做 URL seeding，再把筛选后的 URL 交给 `arun_many()`。

---

## 20. 推荐起步配置

### 20.1 小机器 / NAS / 低内存

```python
MemoryAdaptiveDispatcher(
    memory_threshold_percent=70.0,
    max_session_permit=3,
)
```

### 20.2 普通开发电脑

```python
MemoryAdaptiveDispatcher(
    memory_threshold_percent=75.0,
    max_session_permit=6,
)
```

### 20.3 高性能服务器

```python
MemoryAdaptiveDispatcher(
    memory_threshold_percent=80.0,
    max_session_permit=12,
)
```

实际并发上限取决于：

- 页面复杂度；
- 是否执行 JS；
- 是否截图 / PDF；
- 是否使用代理；
- 机器 CPU / 内存；
- 目标站点限流策略。

---

## 21. 参考来源

- Crawl4AI GitHub 仓库：<https://github.com/unclecode/crawl4ai>
- 官方 Quick Start：<https://docs.crawl4ai.com/core/quickstart/>
- 官方 Browser / Crawler Config：<https://docs.crawl4ai.com/core/browser-crawler-config/>
- 官方 Cache Modes：<https://docs.crawl4ai.com/core/cache-modes/>
- 官方 arun 参数文档：<https://docs.crawl4ai.com/api/arun/>
- 官方 arun_many 文档：<https://docs.crawl4ai.com/api/arun_many/>
- 官方 Multi-URL Crawling：<https://docs.crawl4ai.com/advanced/multi-url-crawling/>
- 官方 Deep Crawling：<https://docs.crawl4ai.com/core/deep-crawling/>
- 官方 URL Seeding：<https://docs.crawl4ai.com/core/url-seeding/>
- 官方 Docker / Self-Hosting：<https://docs.crawl4ai.com/core/self-hosting/>

---

## 22. 最终推荐方案

如果你的目标是“快速、大批量、可归档地抓取网页并供 AI 使用”，推荐默认采用：

```text
URL 列表 / URL Seeding
        ↓
arun_many() 并发爬取
        ↓
MemoryAdaptiveDispatcher 控制并发和内存
        ↓
text_mode + light_mode + avoid_ads + avoid_css 提速
        ↓
CacheMode.ENABLED 做重复任务加速
        ↓
Markdown 保存到 output/markdown
        ↓
必要时再用 CSS JSON / LLM 做结构化抽取
```

这套方案比单 URL 循环 `arun()` 更适合生产使用，也更能发挥 Crawl4AI 的项目优势。
