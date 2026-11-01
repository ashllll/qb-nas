# QBNAS 剪贴板代理（Mac 端）

菜单栏小代理：**只读剪贴板文本**，只认 `magnet:` 和单个 `http(s)://` 网址，通过 `POST /api/v1/ingest` 交给 NAS。
解析、抓取、分类、入库、下载全在 NAS 上；这台 Mac 只负责“看见并上报”。

## 组成

| 路径 | 作用 |
| --- | --- |
| `Sources/Core/Classifier.swift` | 纯函数：剪贴板文本 → 磁力/网址；infohash 归一化（hex/base32）、sha256 摘要 |
| `Sources/Core/QueueStore.swift` | 本地队列与最近结果；只存匹配到的链接，`state.json` 权限 600，原子写 |
| `Sources/Core/APIClient.swift` | `GET /api/v1/health`、`POST /api/v1/ingest`（`X-API-Key`，契约见《约定.md》） |
| `Sources/Core/AgentCore.swift` | 状态机：每秒轮询 → 去重 → 入队 → 发送 → 退避重试；离线/恢复/暂停/授权全在这里 |
| `Sources/Core/Settings.swift` | 地址/暂停放 UserDefaults；API Key 只从钥匙串读 |
| `Sources/Core/Keychain.swift` | 钥匙串条目 `com.qbnas.macagent` / `apiKey` |
| `Sources/Core/LaunchAgent.swift` | 唯一的登录项 `com.qbnas.macagent`（plist 生成、加载、卸载） |
| `Sources/Core/Installer.swift` | 自安装（复制到 ~/Applications + 登录项）与卸载脚本 |
| `Sources/Core/MenuModel.swift` | 菜单文案纯函数（在线/离线/暂停/没 key/最近结果） |
| `Sources/App/*` | AppKit 菜单栏、状态图标、SwiftUI 设置/自检/安装窗口、无头模式 |
| `Sources/Tests/main.swift` | 58 项纯逻辑自测 |
| `build.sh` | 一条命令出 `.app`（swiftc + iconutil + ad-hoc 签名），不需要 Xcode 工程 |
| `Tools/make_icon.swift` | 生成 AppIcon.icns |

## 构建与自测

```bash
cd 代码/mac_agent
./build.sh                 # → build/NAS 剪贴板代理.app
swiftc -O -swift-version 5 Sources/Core/*.swift Sources/Tests/main.swift -o build/selftest && ./build/selftest
```

端到端验收（真剪贴板 + 假 NAS + 无头模式，46 项断言）在公共工具：

```bash
cd ../../../../.blueprint-tmp/公共工具     # 即 $BLUEPRINT_TOOLS
./验收Mac代理.sh
```

## 行为细节（做的时候定下的规则）

- **只认两种输入**：文本里出现 `magnet:?xt=urn:btih:` 就取那个 token；整段文本必须是一个 `http(s)://` 网址才算（一句话里夹网址不收，避免把整段文档里的链接都收走）。其余内容连内容都不读进日志。
- **标准上限**：扫描上限 16 KB、网址上限 2048 字符；超了直接忽略。
- **去重**：本地按 sha256 与归一化 infohash 记 300 秒窗口；队列里没发出去的同一个 infohash 不会重复入队。NAS 侧再按 `event_id` 幂等、按 infohash 归一。
- **重启不重收**：启动时把当前剪贴板 changeCount 记为“已处理”，所以登录自启 / 退出重开都不会把旧内容再收一遍。
- **离线**：健康探测在线 10 秒一次、离线 4 秒一次；发送失败按 3/6/12/30/60/120/300 秒退避（带抖动），恢复在线时把整个队列提前唤醒。
- **暂停**：暂停期间不读剪贴板，只记 changeCount；恢复后不会补收暂停时复制的内容。
- **服务端拒收**（HTTP 400/404/422，或 `status=rejected`）：记为结论出队，不无限重试；原因写进最近结果。
- **API Key 被拒**（401/403）：图标变红、队列保留、60 秒后再试；在「设置」里换 key 后自动恢复。
- **不落全文**：`state.json` 只有匹配到的链接；`status.json` 只有状态与短摘要；`agent.log` 只写类型、哈希前 8 位、域名、结果。

## 坑

- **沙箱内跑不了安装与钥匙串**：本项目工具环境的 shell 被限制写 `~/Applications`、`~/Library/Application Support`，也用不了 Keychain，所以真机安装由用户双击 app 完成（app 自带安装窗口），Keychain 真机路径需要在真机上点一次「安装」和一次「保存 Key」。
- **ad-hoc 签名**：每次重新构建签名都会变，钥匙串可能会再问一次“允许访问”；本机自用可接受，分发要正式签名。
- **ATS**：`Info.plist` 里 `NSAllowsArbitraryLoads=true`，因为要访问局域网 `http://192.168.20.193:8899`。
- **菜单栏图标颜色**：图标是实时绘制的非 template 图，浅色/深色菜单栏都能看清；状态点颜色才是关键信息。
- **macOS 可能弹一次剪贴板访问提示**：允许即可，和辅助功能权限无关。
