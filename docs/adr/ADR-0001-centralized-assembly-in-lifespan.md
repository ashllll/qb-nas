# ADR-0001: Centralized Assembly in FastAPI Lifespan

## Status

Accepted

## Context

`magnet_harvester/main.py` had grown to 547 lines with 8 distinct responsibilities:

- FastAPI route definitions (13 endpoints)
- WebSocket connection management
- qBittorrent background sync loop
- Agent tool executor dispatch
- System stats tracking
- 9 module-level global variables (`_store`, `_bus`, `_pipeline`, etc.)
- Item serialization helpers
- Application lifespan (startup/shutdown)

Architecture analysis showed `main.py` as a supernode with 170+ connected nodes spanning 8 communities. This centralization created three problems:

1. **No real seams** — Services like `_qbit_sync_loop` and `_ws_broadcast` accessed global variables directly, making them untestable without monkeypatching.
2. **Tight coupling across community boundaries** — Community 5 (FastAPI Server) absorbed nodes from Community 0 (Event Bus) and Community 1 (qBittorrent Integration) because `main.py` imported from both.
3. **Private field mutation** — `RuntimeContext.replace_qbit()` reached into `HarvestPipeline._qbit` to swap the qBittorrent client at runtime, with no public seam.

The `deletion test` for `main.py` showed that deleting it would eliminate routing, WebSocket broadcasting, qB sync, stats tracking, tool execution, and config management simultaneously — confirming it was a shallow "everything file" rather than a deep module.

## Decision

Split `main.py` into cohesive modules **and adopt centralized assembly in `lifespan()` as the only place components are created and wired together**.

Specifically:

1. **No module-level global variables** in `main.py` or any split-out module.
2. **`lifespan()` is the sole assembler** — it creates all components, instantiates service classes with explicit constructor dependencies, and starts/stops them.
3. **All split-out services receive dependencies via constructor injection**:
   - `QBitSyncLoop(qbit_client, store, bus)`
   - `WSBroadcaster(bus)`
   - `UserActionExecutor(store, pipeline, task_manager, transitions, stats)`
   - `ItemQueryExecutor(store)` for read-only item query formatting
   - `ObservabilitySnapshot(store, qbit, stats, broadcaster, error_handler)`
   - `BGTaskManager()`
4. **`AppContext` remains the single dependency container** — routed through `app.state.ctx`, retrieved via `Depends(get_context)`.
5. **`HarvestPipeline` gains a public `replace_download_phase()` method** — eliminating the private-field mutation from `RuntimeContext`.

The resulting structure:

```
magnet_harvester/
├── main.py              # FastAPI app + lifespan (sole assembler)
├── context/
│   └── app_context.py   # AppContext, RuntimeContext, get_context
├── api/
│   ├── routes.py        # REST endpoints
│   └── websocket.py     # WebSocket handler + WSBroadcaster
├── services/
│   ├── qbit_sync.py     # QBitSyncLoop
│   ├── user_actions.py  # UserActionExecutor
│   ├── item_queries.py  # ItemQueryExecutor
│   ├── observability.py # ObservabilitySnapshot
│   └── stats.py         # SystemStats
└── utils/
    ├── bg_tasks.py      # BGTaskManager
    └── serializers.py   # _item_summary, _item_payload
```

## Consequences

### Positive

- **Testability**: Each module is testable through its public interface with manually constructed dependencies (FakeStore, NullBus, FakePipeline, etc.). No monkeypatching required.
- **Locality**: Change WebSocket logic without touching qB sync code. Change tool dispatch without touching routes.
- **Leverage**: `api/routes.py` exports ~13 route definitions; implementation hidden behind `Depends(get_context)`.
- **Clear dependency graph**: The entire wiring is visible in one place (`lifespan`), making it easy to understand how components connect.

### Negative

- **`lifespan()` grows longer** (~40-50 lines of assembly code). Adding a new service requires editing `lifespan`.
- **One file to rule them all**: If `lifespan` becomes too large, it becomes a new shallow module. The current design keeps it under control by limiting its responsibility to "create and wire only."

## Alternatives Considered

### Alternative A: Decentralized DI (each module creates its own dependencies)

Each module (routes, websocket, sync) would create its own store/bus/pipeline instances. Rejected because:

- Would duplicate component creation logic across files.
- Would make the dependency graph impossible to trace — no single place shows how the app is wired.
- Would break the `AppContext` seam that `test_appcontext.py` already relies on.

### Alternative B: Keep module-level globals, only physical file split

Split `main.py` into multiple files but keep `_store`, `_bus`, etc. as module-level globals in `main.py`, with other files importing them. Rejected because:

- Does not create real seams — the new files still depend on `main.py` internals.
- Tests would still require monkeypatching.
- Does not solve the community overlap problem.

## Related

- `CONTEXT.md` — glossary terms `WSBroadcaster`, `QBitSyncLoop`, `UserActionExecutor`, `ItemQueryExecutor`, `ObservabilitySnapshot`, `BGTaskManager` added.
- `pipeline.py` — `HarvestPipeline.replace_download_phase()` public method added.
