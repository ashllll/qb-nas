# Scrapling 全量爬虫迁移规格

> **状态：已实现（2026-06）**。当前 `magnet_harvester/crawler.py` + `scrapling_spider.py` 已按本规格落地；本文件保留为规格记录。

## 目标

由 Scrapling Spider 完整负责页面请求队列、并发、按域限流、重试、深度跟进、去重、
robots.txt、动态浏览器会话和流式结果。项目只保留 URL 安全准入、详情页业务筛选、
磁力解析、分辨率过滤以及对上层稳定的爬虫事件协议。

## 技术边界

- 使用锁定在兼容 `0.4.x` 范围内的 `scrapling[fetchers]`。
- `MagnetCrawler.crawl(url, depth)` 的异步事件接口保持不变。
- Scrapling `Spider.stream()` 是唯一的多页面调度入口。
- Scrapling `Request`/`Response.follow()` 负责请求入队、指纹去重和回调。
- Scrapling `AsyncDynamicSession` 负责动态页面加载，项目通过 `page_action` 执行业务所需的
  滚动、弹窗移除、iframe 和 Shadow DOM 展平。
- `CrawlTargetAdmission` 必须在种子 URL、跟进 URL和最终响应 URL处执行。
- Scrapling 浏览器导航、重定向、子资源和 WebSocket 请求必须在实际发出前执行网络准入，
  并禁用可绕过 Playwright 路由的 Service Worker。
- `MagnetSourceExtractor` 继续负责磁力业务解析，不复制到 Spider 框架内部。

## 配置映射

- `CRAWLER_CONCURRENCY` -> `Spider.concurrent_requests` 和动态会话 `max_pages`。
- `CRAWLER_MAX_DETAIL_LINKS` -> Spider 允许调度的详情页上限。
- `CRAWLER_MAX_RETRIES` -> `Spider.max_blocked_retries` 和浏览器会话网络重试。
- `CRAWLER_CHECK_ROBOTS_TXT` -> `Spider.robots_txt_obey`。
- `CRAWLER_DELAY_BEFORE_HTML` -> Scrapling fetch 参数 `wait`。
- 不具备真实行为的旧配置必须删除，不得保留为假开关。

## 测试策略

- 单元测试验证 Spider 产出页面项目、跟进请求、安全过滤、深度和页面上限。
- 适配器测试验证 `MagnetCrawler` 消费 `Spider.stream()` 并保持事件协议。
- 中型浏览器测试验证动态页面策略可由 Scrapling `page_action` 执行。
- 完整运行 Ruff 和 pytest，现有 API、Pipeline、WebSocket 行为不得回归。

## 边界

- 始终执行：类型标注、取消传播、资源关闭、SSRF 校验、流式输出。
- 需要另行授权：代理服务、验证码服务、外部持久化 checkpoint。
- 禁止执行：绕过站点访问控制、关闭安全准入、静默访问私网地址。

## 验收标准

1. `crawler.py` 不再包含本地 BFS 请求调度或直接批量 `session.fetch()`。
2. 所有根页面和详情页请求均由 Scrapling Spider 引擎执行。
3. 并发、重试、robots.txt、链接去重和流式输出映射到 Scrapling。
4. 上层仍收到 `found`、`progress`、`error`、`done` 事件。
5. 消费者提前关闭流时，Scrapling crawl 和浏览器会话能够取消并关闭。
6. 无效配置被实现或移除，依赖不会自动跨越 Scrapling 0.4 主次版本边界。
7. 相关测试、完整测试和 Ruff 全部通过。
