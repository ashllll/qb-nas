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
