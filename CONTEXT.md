# Context

## Glossary

### Magnet item

A discovered magnet link plus the metadata needed to classify it, show it in the UI, and submit it to qBittorrent. Its lifecycle is represented by `TaskStatus`.

### Crawl pipeline

The orchestration that crawls a URL, stores new Magnet items, classifies them, and optionally submits them to qBittorrent.

### MagnetItemTransitions

The module responsible for applying a Magnet item state change to `ItemStore` and publishing the matching `MessageBus` events in observable order.

### RuntimeContext

The module responsible for keeping runtime dependencies aligned when adapters are replaced during application lifetime.

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

### ToolExecutor

The module that dispatches agent tool calls (get_stats, list_items, start_crawl, add_to_queue, reclassify_item, search_items, clear_all) to the appropriate `ItemStore` or `HarvestPipeline` operations.

### BGTaskManager

The utility module that wraps `asyncio.create_task` with exception logging via `add_done_callback`, providing a uniform way to spawn and monitor background coroutines.
