# Magnet Harvester 设计规范

> 版本: v1.0.0
> 适用于 `static/index.html` 及其未来拆分出的前端资源。

---

## 产品类型

NAS 资源工作台 —— 后台工具型 Web App，单页应用，三栏布局的数据控制面板。

核心功能：发起网页爬取采集磁力链接 → 本地规则分类 → 投递至 qBittorrent 下载。

## 目标用户

自建 NAS 的技术用户，熟悉 qBittorrent / 种子 / 磁力链接生态，通常在桌面浏览器中管理资源采集与下载队列。

## 设计气质

- **精密工具感** —— 界面像光谱仪或磁力计的仪器面板，而非消费级 SaaS
- **克制** —— 信息密度适中，装饰服务于信息理解，不堆砌视觉元素
- **暗色优先** —— NAS 管理员的原生栖息地；亮色模式作为高亮度环境的临床化替代
- **磁力隐喻** —— 以磁场双极（北极靛紫 / 南极青绿）贯穿色彩与交互

## 布局规则

- 全窗口三栏网格：`260px / 1fr / 280px`（桌面端 ≥1080px）
- 中等屏幕（760–1080px）：`240px / 1fr`，隐藏右侧检查器
- 移动端（<760px）：单栏，三视图通过底部导航切换
- 三栏分别承载：操控面板（采集 + 统计 + 日志）、资源库（表格）、检查器（设置 + 状态）
- 不使用毛玻璃（backdrop-filter），改用分层不透明度叠加构建深度
- 面板间用 1px 半透明边框分隔，微量阴影区分层级
- 页面无滚动条——各面板内部独立滚动

## 色彩规则

### 暗色模式（默认）

#### 空间层
| Token | 色值 | 用途 |
|---|---|---|
| `--space-0` | `#060A0E` | 最深底层（body bg） |
| `--space-1` | `#0C1117` | 主背景（window bg） |
| `--space-2` | `#131A22` | 抬升面板（sidebar、inspector bg） |
| `--space-3` | `#1B2330` | 悬停 / 激活态 |
| `--space-4` | `#242E3B` | 边框强调 |

#### 磁极强调色
| Token | 色值 | 用途 |
|---|---|---|
| `--pole-north` | `#7C6FF0` | 北极——主强调色、主要按钮、链接 |
| `--pole-south` | `#14C9C9` | 南极——聚焦环、选中态、次强调 |
| `--north-soft` | `#A599F5` | 北极辉光 |
| `--south-soft` | `#5CECEC` | 南极辉光 |
| `--north-muted` | `rgba(124,111,240,0.12)` | 北极背景染（悬停等） |
| `--south-muted` | `rgba(20,201,201,0.10)` | 南极背景染（选中等） |

#### 文本色
| Token | 色值 | 用途 |
|---|---|---|
| `--text-primary` | `#E6EDF3` | 主文字（对比度约 14:1） |
| `--text-secondary` | `#8D9BAE` | 辅助文字、标签 |
| `--text-tertiary` | `#4B5A6E` | 弱化文字、占位符 |

#### 边框色
| Token | 色值 | 用途 |
|---|---|---|
| `--border-subtle` | `rgba(230,237,243,0.06)` | 微妙分隔线 |
| `--border-normal` | `rgba(230,237,243,0.10)` | 标准边框、表格线 |
| `--border-strong` | `rgba(230,237,243,0.16)` | 强调边框、聚焦态 |

#### 功能色
| Token | 色值 | 用途 |
|---|---|---|
| `--success` | `#2DA44E` | 成功 / 在线 / 完成 |
| `--success-muted` | `rgba(45,164,78,0.12)` | 成功背景染 |
| `--warning` | `#D29922` | 警告 / 进行中 |
| `--warning-muted` | `rgba(210,153,34,0.12)` | 警告背景染 |
| `--danger` | `#F85149` | 错误 / 危险 / 离线 |
| `--danger-muted` | `rgba(248,81,73,0.10)` | 危险背景染 |

#### 分类色（磁力谱——在色相环上等距分布）
| Token | 色值 | 用途 |
|---|---|---|
| `--cat-电影` | `#58A6FF` | 电影分类 |
| `--cat-电视剧` | `#BC8CFF` | 电视剧分类 |
| `--cat-动漫` | `#FF967D` | 动漫分类 |
| `--cat-音乐` | `#3FB950` | 音乐分类 |
| `--cat-游戏` | `#F778BA` | 游戏分类 |
| `--cat-软件` | `#D29922` | 软件分类 |
| `--cat-综艺` | `#79C0FF` | 综艺分类 |
| `--cat-纪录片` | `#14C9C9` | 纪录片分类 |
| `--cat-其他` | `#8D9BAE` | 其他分类 |
| `--cat-待分类` | `#4B5A6E` | 待分类 |

### 亮色模式

亮色模式通过 `prefers-color-scheme: light` 触发，为全套反转色板。基础色变为 `#F6F8FA` → `#FFFFFF` 层次，强调色和分类色保持不变以保证品牌识别。

#### 空间层（亮色）
| Token | 色值 | 用途 |
|---|---|---|
| `--space-0` | `#F0F2F5` | 最深底层 |
| `--space-1` | `#F6F8FA` | 主背景 |
| `--space-2` | `#FFFFFF` | 抬升面板 |
| `--space-3` | `#F0F2F5` | 悬停 / 激活态 |
| `--space-4` | `#D8DEE4` | 边框强调 |

#### 文本色（亮色）
| Token | 色值 | 用途 |
|---|---|---|
| `--text-primary` | `#1A2028` | 主文字 |
| `--text-secondary` | `#576270` | 辅助文字 |
| `--text-tertiary` | `#8D9BAE` | 弱化文字 |

#### 边框色（亮色）
| Token | 色值 | 用途 |
|---|---|---|
| `--border-subtle` | `rgba(26,32,40,0.06)` | 微妙分隔线 |
| `--border-normal` | `rgba(26,32,40,0.10)` | 标准边框 |
| `--border-strong` | `rgba(26,32,40,0.16)` | 强调边框 |

## 字体规则

| 用途 | 字体栈 | 字号 | 字重 | 行高 |
|---|---|---|---|---|
| 品牌名 | `"Space Grotesk", system-ui, -apple-system, sans-serif` | 14px | 700 | 1.2 |
| 面板标题 | `system-ui, -apple-system, "Segoe UI", sans-serif` | 11px | 600 | 1.3 |
| 正文 / 控件 | `system-ui, -apple-system, "Segoe UI", sans-serif` | 13px | 400 | 1.4 |
| 表格内容 | `system-ui, -apple-system, "Segoe UI", sans-serif` | 13px | 400 | 1.3 |
| 表格资源名 | `system-ui, -apple-system, "Segoe UI", sans-serif` | 13px | 600 | 1.3 |
| 统计数字 | `"JetBrains Mono", "SF Mono", "Cascadia Code", "Consolas", monospace` | 20px | 600 | 1.2 |
| 哈希 / 大小 | `"JetBrains Mono", "SF Mono", "Cascadia Code", "Consolas", monospace` | 11px | 400 | 1.3 |
| 日志 | `"JetBrains Mono", "SF Mono", "Cascadia Code", "Consolas", monospace` | 11px | 400 | 1.55 |
| 辅助 / 标签 | `system-ui, -apple-system, "Segoe UI", sans-serif` | 10px | 400 | 1.3 |

### 数字展示规则
- 统计数字使用等宽字体 `tabular-nums`，保证数字对齐
- 哈希截断显示（前 16 字符 + …），使用等宽字体
- 文件大小保持原始格式，使用等宽字体

### 字重规则
- 标题：600 或 700
- 正文：400
- 强调（表格资源名、按钮文字）：600
- 不使用 300 以下的轻字重

## 组件规则

### 按钮
- `min-height: 32px`，`padding: 0 12px`，`border-radius: 6px`
- **primary**：`--pole-north` 背景 + 白色文字 + 同色边框，悬停加深至 `#6B5ED9`，按下 `scale(0.97)`
- **default**：透明背景 + `--border-normal` 边框 + `--text-primary` 文字，悬停背景变为 `--space-3`
- **danger**：透明背景 + `--danger` 文字 + `--danger` 边框（30% 不透明度），悬停背景变为 `--danger-muted`
- **icon-only**：`width: 32px`，`padding: 0`，使用 `justify-content: center`
- **full**：`width: 100%`
- **loading**：文字旁出现旋转图标（纯 CSS `@keyframes spin`）
- **disabled**：`opacity: 0.4`，`cursor: default`，不响应悬停

### 输入框
- `height: 34px`，`padding: 0 10px`，`border-radius: 6px`
- 背景 `--space-1`，边框 `--border-normal`，文字色 `--text-primary`
- `::placeholder` 颜色 `--text-tertiary`
- **聚焦**：边框变为 `--pole-south`，外发光 `0 0 0 3px var(--south-muted)`
- **错误**：边框变为 `--danger`

### 表格
- 行高 48px
- 表头 sticky（`top: 0`），背景 `--space-2`，文字 `--text-secondary`，字号 10px，font-weight 600
- 表体行：底部 1px `--border-subtle` 分隔
- **行悬停**：背景 `--north-muted` + 左侧 2px `--pole-north` 竖线渐入
- **行选中**：背景 `--south-muted` + 左侧 2px `--pole-south` 实线
- 复选框使用 `accent-color: var(--pole-north)`

### 分类 Chip
- `border-radius: 5px`，`padding: 2px 8px`，`font-size: 10px`，`font-weight: 600`
- 各类别使用独立的前景色 + 半透明背景（使用分类色 + 对应 muted 背景）
- 待分类状态使用 `--text-tertiary` + `--space-3` 背景

### 开关
- 宽 34px，高 20px，`border-radius: 10px`
- **关闭态**：轨道 `--space-4`，滑块白色 `left: 2px`
- **开启态**：轨道 `--pole-north`，滑块白色 `left: 14px`
- 滑块 `width: 16px`，`height: 16px`，`border-radius: 50%`，`box-shadow: 0 1px 3px rgba(0,0,0,0.3)`
- 过渡：`background 0.18s ease`，`transform 0.18s ease`

### Toast
- `border-radius: 6px`，`padding: 11px 12px`
- 左侧 3px 色条：success=绿 / error=红 / info=青
- 入场：从右侧滑入，`@keyframes toast-in`（200ms ease-out）
- 自动消失：4.2s 后移除
- 不使用 emoji，使用 SVG 图标

### 弹窗（Dialog）
- `border-radius: 8px`，背景 `--space-2`，`border: 1px solid var(--border-normal)`
- `::backdrop`：`rgba(6,10,14,0.6)`
- 入场使用原生 `<dialog>` 的 `showModal()`（浏览器处理动画）
- 标题：`font-size: 15px`，`font-weight: 700`
- 操作按钮右对齐，危险操作使用 danger 按钮样式
- 支持 Escape 关闭

### 空状态
- 居中布局，最大宽度 280px
- 图标：48×48 圆角矩形背景 + 单色 SVG 图标（使用 `--pole-north` 色）
- 标题：13px，700 字重，`--text-primary`
- 引导文案：11px，`--text-secondary`，说明下一步操作

## 交互规则

### 基础交互
- **hover**：120ms ease 过渡，背景色微变
- **focus-visible**：`outline: 2px solid var(--pole-south)`，`outline-offset: 2px`
- **active（按下）**：`transform: scale(0.97)`，80ms ease
- **disabled**：`opacity: 0.4`，`cursor: default`，移除所有交互反馈

### 加载状态
- 按钮 loading：图标旋转 + 文字变为操作进行中描述（如"爬取中"、"发送中"）
- 全局进度条：固定在页面顶部的 2px 蓝色条，宽度由 JS 控制

### 选中状态
- 表格行选中：左侧 `--pole-south` 竖线 + 行背景 `--south-muted`
- 分类标签选中：背景 `--space-3` + 文字 `--text-primary`，无阴影
- 开关选中：轨道变为 `--pole-north`

### 弹窗规则
- 使用原生 `<dialog>` 元素
- 点击 `::backdrop` 不自动关闭（需要明确操作）
- Escape 键关闭
- 危险操作（如清空）需要确认弹窗

### 键盘快捷键
- `/`：聚焦搜索框
- `Escape`：关闭弹窗（如果打开）
- Enter（在 URL 输入框中）：发起爬取

## 动效规则

- 所有过渡统一使用 `ease` 或 `ease-out`
- 时长尺度：`fast: 120ms`、`normal: 200ms`、`slow: 400ms`
- Canvas 磁力场线动画：仅在 WebSocket 事件到达时短暂加速（400ms）+ 增亮，平时极慢呼吸（周期约 8s）
- 新表格行入场不做动效（避免性能问题）
- Toast 入场 200ms ease-out，不设退场动画
- 尊重 `prefers-reduced-motion`：所有动效降级为 `0.01ms`

## 文案风格

- 按钮：动词开头，具体可预期——"开始爬取"、"下载选中"、"保存并测试"、"重新分类"、"清空资源"
- 错误提示：说明发生了什么 + 用户如何修复，不暴露技术堆栈。格式："无法连接 qBittorrent · 请检查地址和凭据"
- 成功提示：简洁确认——"qBittorrent 连接成功"、"下载任务已提交"
- 空状态引导：说明当前状态 + 下一步操作——"资源库为空 · 从左侧创建采集任务，或开启剪贴板监控"
- 面板标题：名词短语——"新建采集任务"、"任务概览"、"活动记录"、"连接设置"、"访问安全"、"运行状态"
- 使用用户熟悉的术语：qB / qBittorrent（而非"下载客户端"）、磁力链接（而非"magnet URI"）

## 无障碍要求

- 主文字对比度 ≥ 12:1（暗色模式下 `--text-primary` 对 `--space-1`）
- 所有交互元素可通过 Tab 键访问
- 焦点样式 `outline: 2px solid var(--pole-south)` + `outline-offset: 2px`，始终可见
- 所有输入框有 `<label>` 关联
- 状态指示器始终配文字标签（不只依赖颜色传达信息）
- 纯图标按钮有 `aria-label` 或 `title` 属性
- 触控目标最小 32×32px（按钮默认满足）
- 表格使用语义化 `<table>` + `<thead>` + `<tbody>`
- 对话框使用原生 `<dialog>` 元素

## 禁止事项

- ❌ 不使用毛玻璃效果（`backdrop-filter: blur()`）
- ❌ 不使用大面积紫色 / 蓝色渐变作为背景
- ❌ 不把每个区块包装成浮起卡片（卡片仅用于统计数字和弹窗）
- ❌ 不使用纯装饰插画作为主要视觉元素
- ❌ 不做无功能意义的动效（磁力场动画响应真实事件，有功能意义）
- ❌ 不使用负字间距（`letter-spacing` 不低于 0）
- ❌ 不使用视口单位控制字体大小（不用 `vw` 做 `font-size`）
- ❌ 不引入任何外部 CSS / JS 框架或 CDN 依赖——保持单文件自包含
- ❌ 不改变 JavaScript 逻辑或 API 合约——纯视觉层重设计
