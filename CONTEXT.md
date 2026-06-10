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
