# 审查发现

本文件仅记录候选项与验证证据；未完成双重验证前不作为结论。

## C-001：完成回调可覆盖已离开 `adding` 的下载状态

- 候选位置：`magnet_harvester/transitions.py`，`DownloadTransitions.submitted()`。
- 证据一（可复现）：先将条目置为 `error`，再调用 `download_submitted()`，状态被无条件改写为 `queued`。
- 证据二（业务调用链）：`HarvestPipeline._download_single_item()` 在 qB 提交完成后调用该转换；同时 `QBitSyncLoop._reconcile_batch()` 可把同一条目更新为 `error/removed`。后到达的提交回调会覆盖同步得到的终态，且“已移除”增量只消费一次，条目可能长期卡在 `queued`。
- 拟定修复：只允许 `adding -> queued`；不满足来源状态时不写入、不发事件。以转换 module 集中维护该不变量。

## C-002：分类结果可覆盖非 `classifying` 的状态

- 候选位置：`magnet_harvester/transitions.py`，`ClassificationTransitions.classified()` / `failed()`。
- 证据一（可复现）：对 `pending` 条目直接调用 `classified()` 仍会写入分类结果；这说明回调没有来源状态保护。
- 证据二（业务调用链）：`HarvestPipeline._stream_classify()` 在后台任务中消费流式回调；重复 reclassify 请求可让第二个回调在第一个回调已将条目恢复为 `pending` 后再次写入，因此出现重复事件与最后写入者获胜。
- 拟定修复：只允许 `classifying -> pending` 的成功/失败完成；重复或过期回调成为无操作。

## C-003：配置 CORS 时应用无法启动

- 候选位置：`magnet_harvester/main.py`，lifespan 内的 `app.add_middleware()`。
- 证据一（可复现）：在 lifespan 内向最小 FastAPI app 添加 CORS middleware，`TestClient` 启动抛出 `RuntimeError: Cannot add middleware after an application has started`。
- 证据二（框架行为）：当前环境 Starlette 的 `add_middleware()` 在 `middleware_stack` 已创建后明确拒绝注册；lifespan 正处于这一时序之后。
- 拟定修复：在模块级 app 创建后、挂载路由前配置 CORS；lifespan 只处理运行时资源。

## C-004：SQLite adapter 在异步业务路径阻塞事件循环

- 候选位置：`store.py` 的同步 `SQLiteItemStore`，以及 transitions、pipeline、qB sync、查询与 WebSocket 初始化等 async 调用方。
- 证据一（可复现）：对同一 SQLite 数据库持有独占写锁 250ms 后，在协程内直接调用 `store.update()`，调用等待约 264ms；并发 ticker 在 350ms 内仅运行 8 次，证明事件循环被同步 SQLite 等待占用。
- 证据二（业务调用链）：运行时可通过 `STORE_BACKEND=sqlite` 组装 SQLite adapter；所有上述业务 module 均直接在 coroutine 中调用其同步 interface，且该 adapter 的连接配置包含 5 秒 busy timeout。
- 拟定修复：保持现有同步 adapter interface 和内存路径不变；为 async 调用方提供一个共享调用入口，只把声明为阻塞 I/O 的 adapter 操作交给 `asyncio.to_thread`。
- 实施结果：`call_store()` / `store_value()` 是唯一的 async crossing seam；`SQLiteItemStore.blocks_event_loop=True` 触发线程调度，内存 adapter 直接调用。所有 async 业务调用点已迁移，且 SQLite 写锁验证中 ticker 在 350ms 内运行 32 次。

## C-005：分类状态准入被拒绝后 pipeline 仍调用分类 adapter

- 候选位置：`pipeline.py` 的 `HarvestPipeline._stream_classify()` 与 `transitions.py` 的 `classification_started()`。
- 证据一（行为测试）：transition 返回拒绝后，`reclassify()` 仍把条目传给 classifier，新增测试在修复前稳定失败。
- 证据二（调用链）：重复分类或并发状态推进可让 transition 拒绝 `classifying` 条目，但旧 interface 不返回决定，pipeline 无法区分成功与拒绝。
- 实施结果：准入 interface 显式返回 `bool`；pipeline 只建立已准入条目的分类批次，全部拒绝时不调用 adapter、不发送空批次事件。

## C-006：状态不变量跨 `get -> check -> update` 泄漏

- 候选位置：`MagnetItemTransitions` 与两个 `ItemStore` adapter。
- 证据一（并发测试）：两个调用被强制同时读取 `pending` 后，旧实现返回两次成功并发送两次 `STORE_CHANGED`。
- 证据二（adapter 语义）：SQLite 的 `get()` 与 `update()` 各自持锁和提交，无法让 transition 的跨调用检查具备原子性；单实例 `_submit_lock` 也不能保护其他 transition 实例。
- 实施结果：`update_if_status()` 成为真实的双 adapter seam；内存 adapter 在同一锁内比较并更新，SQLite adapter 使用带状态条件的部分字段写入。分类、下载提交与 qB 同步回调均已迁移。
- 二次复核：SQLite 测试在两个连接都完成旧状态读取后才释放写入，仍只有一个条件更新成功；触发器模拟的同状态并发字段更新也被保留，证明旧快照不会覆盖无关字段。

## C-007：部分分类准入后 auto-download 仍消费原始 hash 集合

- 候选位置：`pipeline.py` 的 `_stream_classify()` 返回值与 `execute()` 自动下载分支。
- 证据一（行为测试）：批次首项被拒绝、次项成功准入时，旧实现仍把两项都交给下载阶段，拒绝项最终被错误标记。
- 证据二（调用链）：`new_hashes` 表示发现阶段新增条目，并不等同于本轮成功准入且完成分类的条目；并发状态变化会扩大两者差异。
- 实施结果：分类阶段返回经过当前状态、分类结果与错误字段复核的 hash；auto-download 仅消费该集合，拒绝项不再进入下载链路。

## C-008：ItemStore 适配器允许修改不可变主键

- 候选位置：`store.py` 的通用字段更新校验。
- 证据一（跨 adapter 契约）：内存 adapter 更新 `hash` 后对象字段与字典键不一致；SQLite adapter 则会移动数据库主键，两个实现语义分裂。
- 证据二（领域不变量）：`hash` 是去重键、查找键和事件关联键，生命周期内修改会破坏调用方持有的身份引用。
- 实施结果：普通更新和状态条件更新统一拒绝 `hash` 字段；原键与数据保持不变。

## C-009：下载提交使用状态准入前的元数据快照

- 证据：测试在下载准入过程中更新 magnet、category 和 save_path；旧实现仍向 qB 提交准入前字段。进一步把旧 category 设为空后，旧实现甚至在准入前直接拒绝。
- 实施结果：`download_submitting()` 返回进入 `adding` 后的不可变 `MagnetItem` 快照；category 校验和 qB 参数都只使用该快照。

## C-010：手动分类会写回旧 save_path 且可修改活跃条目

- 证据：并发 adapter 在分类更新前写入新路径，旧实现随后把先前读取的路径覆盖回去；adding/queued/downloading/success 条目也会被无条件改分类。
- 实施结果：手动分类通过 `update_if_status()` 只允许 pending/error/skipped，并且只写 category，不再读写 save_path。

## C-011：内存 adapter 泄漏可变领域对象

- 证据：调用方可修改 `get()` 返回对象的 status，绕过 store 锁、transition 和事件发布；SQLite adapter 返回独立对象，语义不一致。
- 实施结果：`MagnetItem` 设为冻结 Pydantic 模型；状态变化继续统一使用验证后的副本更新。

## C-012：crawler 与 clipboard 维护两套摄取编排

- 证据：ClipboardMonitor 自行分类、存储、发布和调度下载，构造器需要 store、classifier、pipeline、action executor、transitions，多处生命周期知识重复。
- 实施结果：`HarvestPipeline.ingest()` 集中去重和分类；`UserActionExecutor.ingest()` 增加受管后台下载；ClipboardMonitor 只依赖一个 ingestion interface。

## C-013：crawl session 不属于应用任务管理器

- 证据：crawl session 使用 `BGTaskManager.spawn()` fallback，应用任务快照和统一 shutdown 看不到该任务。
- 实施结果：`MagnetCrawler` 接收 task spawner，装配时注入唯一 `BGTaskManager`；行为测试确认 crawl-session 经注入 manager 创建。

## C-014：运行时 module 通过 AppContext 外观获取宽依赖

- 证据：`QBitRuntime` 持有完整 context 回引用；`AppRuntime` 的启动关闭也通过 context 转发属性定位 crawler、qB 和 task manager。
- 实施结果：`QBitRuntime` 仅持有 `QBitReplacementTarget`；`AppRuntime` 显式接收生命周期 protocol；生产 routes 直接访问三个语义子容器，顶层转发属性仅保留兼容用途。
