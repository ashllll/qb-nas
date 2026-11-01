# Context

## Glossary

### Magnet item

A discovered magnet link plus the metadata needed to classify it, show it in the UI, and submit it to qBittorrent. Its lifecycle is represented by `TaskStatus`.

### Magnet item store

The source of truth for discovered Magnet items, including their lifecycle state, classification metadata, and download progress.

### Crawl pipeline

The orchestration that crawls a URL, stores new Magnet items, classifies them, and optionally submits them to qBittorrent.

### DynamicPagePolicy

The browser preparation module that removes blocking overlays, scans the full page, and exposes same-origin iframe and shadow DOM content before Magnet extraction.

### Discovery lifecycle

The lifecycle that admits newly discovered Magnet items into the collection and removes the collection when the user clears it.

### Classification lifecycle

The lifecycle that records classification start, success, failure, and manual category changes for a Magnet item.

### Download lifecycle

The lifecycle that records qBittorrent submission and reconciles later download state changes for a Magnet item.

### QBitRuntime

The hot-swap adapter responsible for validating, persisting, and atomically replacing the active qBittorrent configuration. It depends only on the narrow `QBitReplacementTarget` (never the full `AppContext`), built via `AppContext.replacement_target()` / `QBitRuntime.from_context(ctx)`.

### QBitReplacementTarget

The narrow dependency set a qBittorrent hot-swap needs: current client, crawl pipeline, download state sync, observability, plus an `on_qbit_replaced` callback that writes the new client back into the primary container. Keeps `QBitRuntime` decoupled from `AppContext`.

### Event versioning

Every bus/WebSocket event payload carries `updated_at` (naive-local `datetime.now()` ISO string; lexicographic order == chronological order). The frontend `seenAt` version table drops late-arriving stale events so they cannot overwrite newer state.

### Local classification

The local rule-based process that assigns a category and save path to a Magnet item without calling an external AI provider.

### KeywordCategoryRecognizer

The optional helper module that maps configured filename keywords to Local classification results.

### Download state sync

The background process that polls qBittorrent state and reconciles tracked Magnet items with the current torrent snapshot.

### TorrentStatusMapper

The qB lifecycle module that maps qBittorrent torrent state snapshots to Magnet item task status fields.

### WSBroadcaster

The module that manages all active WebSocket client connections and subscribes to `MessageBus` events to broadcast real-time updates to connected clients.

### QBitSyncLoop

The background module that polls qBittorrent state at regular intervals and reconciles tracked Magnet items with the current torrent snapshot.

### ItemQueryExecutor

The read-only query module that formats Magnet item stats, list, and search results from `ItemStore`.

### UserActionExecutor

The command module that executes user-triggered actions shared by HTTP routes and agent tools, including crawl start, download submission, reclassification, manual category changes, and item clearing.

### ObservabilitySnapshot

The query module that builds user-facing runtime snapshots for status, health, and service stats from `ItemStore`, qBittorrent, `SystemStats`, WebSocket state, and error stats.

### BGTaskManager

The utility module that wraps `asyncio.create_task` with exception logging via `add_done_callback`, providing a uniform way to spawn and monitor background coroutines.
