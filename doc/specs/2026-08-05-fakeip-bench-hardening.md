# Fake-IP 收窄与爬虫解析基准 (Fake-IP Hardening & Crawler Parse Benchmark) — 2026-08-05

**Goal:** 收窄 `CRAWLER_ALLOW_FAKE_IP` 的 SSRF 豁免面（字面 IP 不再豁免，DNS 解析结果仍豁免），并为爬虫同步页面解析建立真实性能基准，据基准结果决定是否引入 `asyncio.to_thread`。

**Status:** Approved (user) — 2026-08-05，A+B 方案已批准。

---

## 1. Background

第二轮侦察确认本机 DNS 被 mihomo 劫持（`getaddrinfo('example.com') → 198.18.2.131`，fake-IP 段），因此 `CRAWLER_ALLOW_FAKE_IP=true` 是**必需**的——所有域名解析都返回 198.18.x.x，不豁免则爬虫无法工作。真实风险在于豁免面过宽：

| 风险点                                  | 现状                       | 问题                                                                      |
| --------------------------------------- | -------------------------- | ------------------------------------------------------------------------- |
| 域名解析结果豁免                        | 必需（fake-IP 只服务域名） | 保留                                                                      |
| 字面 IP 提交（URL 直接写 `198.18.x.x`） | 被豁免                     | **无正当理由**：fake-IP 只对域名解析生效，字面 IP 直连不走代理 fake-IP 池 |
| 子资源/详情页请求                       | 同样豁免                   | 页面内资源域名解析也返回 fake-IP，属正常行为，保留                        |

**B 项**：爬虫同步解析路径 `crawler.py:353 → magnet_sources.py:24-41 → magnet_parser.py:183-241`（多正则全扫 + Base64 解码）+ `scrapling_spider.py:149`（css 选择器）全在事件循环内同步执行，无任何性能基准，无法确认阻塞量级。

## 2. Decisions

1. **A — 字面 IP 不再豁免**：`validate_crawl_url(url, allow_fake_ip=True)` 对**字面 IP 形态的 hostname** 不做 fake-IP 豁免（`_validate_hostname` 中若 hostname 可解析为 IP 字面量且属于 `FAKE_IP_NETWORK` → 拒绝）；**DNS 解析结果仍豁免**（`admit()` 中 resolver 返回的地址属于 `FAKE_IP_NETWORK` → 放行）。实现方式：`_validate_hostname` 增加字面 IP 检测分支——若 hostname 本身是 IP 字面量，调用 `_is_unsafe_address(hostname, allow_fake_ip=False)`（不传 flag）；非字面 IP 的域名走原逻辑（`allow_fake_ip` 只在解析结果阶段生效）。
2. **A — 现有测试更新**：`tests/test_url_validator.py:211-217` 的两个字面 IP 用例——`test_accepts_fake_ip_when_flag_enabled`（def 211，断言 213 行）与 `test_accepts_198_19_x_when_flag_enabled`（def 215，断言 217 行）——均断言字面 IP 放行 → 改为断言**字面 IP 拒绝 + 域名（解析为 fake-IP）放行**。
3. **A — 子资源行为不变**：`_guard_browser_request`/`_guard_websocket_request`/`_admit_detail` 继续走 `admit()`（域名解析豁免），无代码改动。仅初始 URL 的字面 IP 收窄。
4. **B — 先基准后修复**：新增 `scripts/bench_crawl_parse.py`（不入 pytest 门禁），合成 0.5/1/5MB HTML 测 `extract_from_text` + css 选择器耗时。**若单次解析 > 100ms**，则修复 `magnet_sources.from_page_result` 走 `asyncio.to_thread`；否则仅保留基准脚本并记录结论。

## 3. Scope

**In scope:**

- `magnet_harvester/utils/url_validator.py` — 字面 IP 不再豁免
- `tests/test_url_validator.py` — 更新字面 IP 用例 + 新增域名解析豁免用例
- `tests/test_api_ssrf.py` — 如有受影响断言同步更新
- `scripts/bench_crawl_parse.py` — 新增基准脚本（不入 pytest）
- `.env.example:36-37` — `CRAWLER_ALLOW_FAKE_IP` 注释补充「仅域名解析豁免，字面 IP 始终拒绝」
- 若基准确认阻塞显著：`magnet_harvester/magnet_sources.py` 的 `from_page_result` 走 `to_thread` + 对应测试

**Out of scope:**

- `CRAWLER_ALLOW_FAKE_IP` 开关本身保留（环境必需）
- 子资源请求豁免行为不变
- 爬虫并发模型/调度重构
- hash 格式校验、CSP 等其他待办

## 4. Acceptance Criteria

1. `validate_crawl_url("http://198.18.2.102/torrents", allow_fake_ip=True)` 抛 `URLValidationError`（字面 IP 拒绝）。
2. 域名提交（如 `http://example.com/`，解析为 198.18.2.131）在 `allow_fake_ip=True` 时通过 `admit()`（DNS 解析豁免）。
3. `allow_fake_ip=False` 时字面 IP 与域名解析均拒绝（行为不变）。
4. `scripts/bench_crawl_parse.py` 可独立运行，输出 0.5/1/5MB 三种规模的解析耗时（ms）。
5. 基准结论决定是否 `to_thread`：若修复，`from_page_result` 调用方改为 `asyncio.to_thread` 且测试覆盖；若不修复，基准结果记录在 spec 备注。
6. 全量门禁通过：pytest、ruff、`git diff --check`。

## 5. Testing Approach

- A 项 TDD：先改 `test_url_validator.py` 两个字面 IP 用例（`test_accepts_fake_ip_when_flag_enabled`、`test_accepts_198_19_x_when_flag_enabled`，RED）→ 实现 `_validate_hostname` 字面 IP 分支（GREEN）→ 新增域名解析豁免用例（用 mock resolver 返回 fake-IP 地址）。
- B 项：基准脚本先行（`scripts/bench_crawl_parse.py`），运行记录数据；若触发修复阈值，按 TDD 补 `to_thread` 测试。
- 修改/新增测试文件：
  - `tests/test_url_validator.py` — 字面 IP 拒绝 + 域名解析豁免
  - `tests/test_api_ssrf.py` — 如有受影响断言更新
  - 若修复 to_thread：`tests/test_magnet_source_extractor.py` 或 `tests/test_crawler_crawl.py`
- 全量门禁：`.venv/bin/python -m pytest tests -q`、`.venv/bin/ruff check magnet_harvester tests`、`git diff --check`。

## 6. Documentation impact

- Feature / user-facing docs introduced: `scripts/bench_crawl_parse.py`（开发者工具，附用法注释）
- Materially amended existing docs: `.env.example:36-37`（`CRAWLER_ALLOW_FAKE_IP` 注释补充"仅域名解析豁免，字面 IP 始终拒绝"）
- Derived / memory docs invalidated: none（AGENTS.md 未描述 fake-IP 细节）

## 7. Open Questions

- 无（A+B 方案已批准；B 项的 to_thread 决策由基准数据驱动，属执行期决策）。
