# Context

## Glossary

### Magnet item

A discovered magnet link plus the metadata needed to classify it, show it in the UI, and submit it to qBittorrent. Its lifecycle is represented by `TaskStatus`.

### Crawl pipeline

The orchestration that accepts Magnet items from crawler and clipboard sources, stores new items,
classifies them, and optionally submits them to qBittorrent through one ingestion interface.

### Magnet ingestion

The `HarvestPipeline.ingest()` interface owns deduplication and classification for non-crawler
sources. `UserActionExecutor.ingest()` adds managed optional download scheduling, so source adapters
do not reproduce lifecycle or background-task logic.

### MagnetItemTransitions

The module responsible for admitting Magnet item lifecycle changes, applying them through
`ItemStore` atomic conditional updates, and publishing matching `MessageBus` events only after a
successful state change.

### ItemStore

The seam for storing and querying Magnet items. The in-memory and SQLite adapters both guarantee
that status comparison and field updates are atomic when a lifecycle transition is admitted.

### RuntimeContext

The module responsible for keeping runtime dependencies aligned when adapters are replaced during application lifetime.

### QBitRuntime

The module responsible for atomically replacing the active qBittorrent adapter through a narrow
`QBitReplacementTarget`, without retaining the full `AppContext`.

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

The owner of application background coroutines, including crawl sessions, pipeline jobs, qB sync,
and clipboard monitoring, from creation through application shutdown.
