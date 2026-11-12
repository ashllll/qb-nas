# 验证层次与验收边界

本文档明确区分两层验证：**自动化测试通过** 与 **生产链路已验证**。
两者不能互相替代 —— “pytest 全绿”只说明代码逻辑符合预期，不证明
真实站点、真实飞牛下载中心、NAS 下载链路可用。

## 1. 自动化测试（pytest，默认门禁）

覆盖范围：

- HTTP MockTransport / FakeQbit、模拟爬虫、NullBus/RecordingBus 下的单元与集成测试
- URL 校验 / SSRF 防护、分类规则链、状态转换与事件、桥接提交与 GID 持久化、状态同步，以及旧 qB 辅助模块
- API 认证与路由、WebSocket 广播与握手认证、剪贴板监控

**能证明**：逻辑正确性、边界处理、并发与回滚语义、协议字段完整性。

**不能证明**：真实站点可抓、桥接能登录飞牛、动态页面渲染、Cookie 注入
生效、NAS 磁盘与下载链路可用、网络延迟/超时下的真实表现。

运行方式：

```bash
python -m pytest tests -q        # 全量
ruff check magnet_harvester tests
```

## 2. 生产链路 smoke 验证（可选，真实环境）

`scripts/smoke_production.py` 针对真实环境执行受控检查：

| 步骤            | 验证内容                                     | 是否写副作用                                                |
| --------------- | -------------------------------------------- | ----------------------------------------------------------- |
| 下载中心连通 | 桥接版本标记与真实下载中心任务统计查询 | 否 |
| 站点抓取        | Scrapling 抓取真实页面并提取 magnet          | 否（仅浏览器访问）                                          |
| 本地分类        | 对抓取到的名称跑真实规则链                   | 否                                                          |
| 提交链路        | add_magnet + 状态轮询（仅 `SMOKE_SUBMIT=1`） | **是**：提交 smoke_test 占位 magnet；任务留在下载中心，需手动清理 |

用法：

```bash
SMOKE_CRAWL_URL="https://example-site/page" \
SMOKE_ARIA2_RPC_URL="http://192.168.20.193:6800/jsonrpc" \
SMOKE_ARIA2_SECRET="****" \
  python scripts/smoke_production.py

# 如需同时验证真实提交（谨慎：会向下载中心添加占位 magnet，验证后需手动清理）
SMOKE_SUBMIT=1 python scripts/smoke_production.py  # 其余变量同上，需先 export
```

可选变量：`SMOKE_SITE_COOKIES='{"domain": "cookie-string"}'` 注入 Cookie；
`SMOKE_CRAWL_TIMEOUT=120` 调整抓取超时（默认 60s）；`SMOKE_ARIA2_SAVE_PATH` 指定 NAS `/volN/...` 下载根目录。
`SMOKE_STATE_DB` 默认 `data/smoke-aria2.db`，持久化占位任务的哈希/GID 对应关系，重跑时沿用。

注意：smoke 使用的 magnet 是占位 hash（仅验证提交与轮询机制，无真实对等体）。
此脚本只验证提交与状态同步，不证明真实内容可下载。下载落盘验收应另选有权下载的有效资源，并在 NAS 下载中心核对文件与路径。

### 提交结果未知时的人工恢复

提交超时、取消或断电后，`aria2_bindings` 中可能留下 `gid IS NULL` 的未决记录。此时自动重试会被拒绝：桥接的任务列表可能没有原磁力/hash，不能凭文件名认领一个任务。先在 NAS 下载中心核对这条**完整磁力**是否已创建，再决定恢复方式。

1. 停止采集中心服务（标准部署为 `docker stop qb-nas-center`），保持下载中心运行。
2. 在 NAS 上运行 `python3`，按实际部署修改下列路径、RPC 地址和完整哈希。先用 SQLite 的备份接口保存数据库，包含仍在 WAL 中的数据；不要只复制主 `.db` 文件。

```python
import sqlite3
from datetime import datetime
from pathlib import Path

path = Path("/vol3/1000/qb-nas/data/magnet_items.db")
endpoint = "http://NAS-IP:6800/jsonrpc"  # 必须与数据库中的实际端点完全一致
hash_key = "完整的40位磁力哈希".lower()
conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True)
backup_path = path.with_name(path.name + "." + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
backup = sqlite3.connect(backup_path)
conn.backup(backup)
backup.close()
print(conn.execute(
    "SELECT endpoint, hash, gid FROM aria2_bindings WHERE endpoint=? AND hash=? AND gid IS NULL",
    (endpoint, hash_key),
).fetchall())
```

3. 只有查到这一条未决记录、且已在 NAS 核对其身份后，选择下面**一种**操作。在同一个 Python 会话内执行；禁止清空整张表或按名称模糊匹配。若不能确认任务身份，保留未决记录。

已确认任务存在并取得对应的精确 GID：

```python
gid = "经核对属于该磁力的16位十六进制GID"
assert len(gid) == 16 and all(c in "0123456789abcdefABCDEF" for c in gid)
with conn:
    result = conn.execute(
        "UPDATE aria2_bindings SET gid=?, complete=0 WHERE endpoint=? AND hash=? AND gid IS NULL",
        (gid.lower(), endpoint, hash_key),
    )
    assert result.rowcount == 1  # 不匹配则事务回滚
```

已确认下载中心没有创建这条任务，允许下次手动重新提交：

```python
with conn:
    result = conn.execute(
        "DELETE FROM aria2_bindings WHERE endpoint=? AND hash=? AND gid IS NULL",
        (endpoint, hash_key),
    )
    assert result.rowcount == 1  # 不匹配则事务回滚
```

4. `conn.close()` 后退出 Python，用 `docker start qb-nas-center` 恢复中心服务。绑定已有 GID 后等待同步；只在确认未创建并清除该未决记录的情况下，在网页手动重试下载。保留备份以便核对。

## 3. WebSocket API Key 的传递

- `/ws` 的 API Key 只接受握手后的第一帧 `{"type":"auth","api_key":"..."}`；
  查询串里的 `api_key` 一律不读（即使值正确也按「没带」拒绝，close 4401）。
  这样 key 不会进入 URL、反代/Uvicorn 访问日志或浏览器历史。
- 必须先 `ws.accept()` 再收第一帧，否则浏览器只能看到 1006/403，
  界面分不清「没带 key」「key 不对」「服务端没配 key」。
- 认证失败（4401）不进入广播器；比较使用 `secrets.compare_digest`（恒定时间），
  日志只记 `api_key_missing`/`api_key_invalid`，不含 key 内容。
- 未配置 `API_KEY` 时 `/ws` 保持开放（本地回环默认部署兼容模式）；
  非本机部署必须配置 `API_KEY`，否则资源名称/来源/下载状态可被未授权订阅。

## 4. 验收结论判定

| 状态                      | 判定                                                                            |
| ------------------------- | ------------------------------------------------------------------------------- |
| pytest 全绿               | 逻辑与协议正确，**可以合入代码**，不能作为生产验收                              |
| smoke 全 PASS（不含提交） | 真实登录、抓取、分类链路可用                                                    |
| smoke 全 PASS（含提交）   | 真实提交与状态轮询链路可用                                                      |
| 生产验收（真机）          | 需在 NAS 上完成：真实下载落盘、断电恢复、长时间运行、磁盘/流量/错误注入后再判定 |

任何情况下，不得把“测试通过”“ERC/DRC 为 0”或“smoke PASS”描述为
“生产链路已完成验收”。
