# 取页改为 HTTP 优先（2026-11-01）

## 问题

磁力链接全部走 `sid="browser"`：每个页面开浏览器、等 1s、滚 8 次。
真站点实测一页 ≈ 1.2s，12 页 9.7s，58 页 71s；而同一站点用
`FetcherSession(impersonate="chrome")` 拿 HTML 只要 0.4–0.6s（列表页 0.7s）。

## 决定

1. **请求层**（`page_fetch.PageFetcher`）：
   - 默认用 `FetcherSession(impersonate="chrome")`，会话长期持有、跨抓取复用；
   - 只有下面的情况才升级到 `AsyncDynamicSession`（同样长期持有、复用）：
     人机校验页（`Just a moment` 等标记）、403/429/503、HTTP 200 但正文是空壳；
   - 详情页不逐页升级：根页 HTTP 成功就整站走 HTTP；整轮一条详情都没取成时，
     才用一次浏览器确认（`site_walker._browser_rescue`），确认无用立刻收手；
   - 每站点「最小间隔 + 并发上限 + 单请求超时 + 指数退避」，失败落 `FailureReason` 枚举；
   - 跟随重定向后重新做 SSRF 准入；浏览器子请求逐个准入（沿用旧行为）。
2. **走查层**（`site_walker.MagnetPageWalker`）：根页 → 详情页，谁先回来谁先交；
   详情页判定用路径段 + 数字 ID，不再用 `?id=` 这类宽匹配（宽匹配会把分页也当详情页）。
3. **汇聚层**（`crawler.MagnetCrawler`）：事件契约不变，`error` 增加 `reason`，
   `done.metrics` 增加 `first_item_ms / browser_used / errors_by_reason / pages`。

## 不变量

- `source_url` = 用户复制的根 URL；`detail_url` = 磁力所在页；
- infohash 归一化成 40 位大写 hex（base32 解码），同一种子只有一条；
- 分辨率过滤默认关闭（`CRAWLER_ALLOWED_RESOLUTIONS=` 空）；
- 删除了 `scrapling_spider.py`（Spider 调度器）；`Spider` 的 SSRF 守卫语义移到
  `page_fetch._guard_page_setup` 与重定向准入。

## 实测（真站点，同一台 Mac，同一 URL）

| | 旧（浏览器） | 新（HTTP 优先） |
|---|---|---|
| 首条磁力 | 19.6s | 2.4s |
| 整轮 | 71.4s | 10.4s |
| 页数 | 58 | 58 |
| 浏览器 | 每页 | 未使用 |
| 条目 | 51 | 51 |

## 已知取舍

- 详情页 403 不再逐页试浏览器（每页 5–10s 且基本无效）；整站都被挡时才用一次浏览器确认。
- 用户复制的是详情页时不再跟「相关资源」，避免一次抓取变成几十条无关请求。
