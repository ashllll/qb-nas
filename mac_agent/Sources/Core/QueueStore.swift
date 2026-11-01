import Foundation

struct QueueEntry: Codable, Equatable {
    let id: String            // event_id（uuid），NAS 侧按它幂等
    let kind: ClipKind
    let value: String
    let capturedAt: String    // ISO8601
    var attempts: Int
    var nextAttemptAt: Double // epoch 秒
    var lastError: String?
}

struct ResultRecord: Codable, Equatable {
    let at: Double
    let kind: String
    let status: String        // accepted / duplicate / rejected / done / error
    let detail: String        // 短摘要：哈希前缀、域名、条目数、原因
}

private struct PersistedState: Codable {
    var version: Int = 1
    var entries: [QueueEntry] = []
    var history: [String: Double] = [:]   // 去重键 -> 最近一次收下的时间
    var results: [ResultRecord] = []
}

/// 队列只保存匹配到的 magnet / 网址，不保存剪贴板全文。
/// 全部状态写在同一个 JSON 里，原子替换 + 0600 权限。
final class QueueStore {
    private let fileURL: URL
    private(set) var entries: [QueueEntry] = []
    private var history: [String: Double] = [:]
    private(set) var results: [ResultRecord] = []

    /// 同一个东西在这么短时间内重复复制，不再重复上报。
    var dedupeWindow: TimeInterval = 300
    private let maxHistory = 500
    private let maxResults = 20

    init(dir: URL) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        self.fileURL = dir.appendingPathComponent("state.json")
        load()
    }

    var queueCount: Int { entries.count }

    // MARK: - 去重

    private func dedupeKeys(for item: ClipItem) -> [String] {
        var keys = ["d:\(item.digest)"]
        if let infohash = item.infohash { keys.append("ih:\(infohash)") }
        return keys
    }

    func shouldAccept(_ item: ClipItem, now: Double) -> Bool {
        for key in dedupeKeys(for: item) {
            if let last = history[key], now - last < dedupeWindow { return false }
        }
        // 队列里还没发出去的同一个 infohash，也不要重复入队
        if let infohash = item.infohash,
           entries.contains(where: { $0.kind == .magnet && $0.value.lowercased().contains(infohash) }) {
            return false
        }
        return true
    }

    func noteAccepted(_ item: ClipItem, now: Double) {
        for key in dedupeKeys(for: item) { history[key] = now }
        trimHistory(now: now)
    }

    @discardableResult
    func enqueue(_ item: ClipItem, now: Double, nextAttemptAt: Double) -> QueueEntry {
        let entry = QueueEntry(id: UUID().uuidString,
                               kind: item.kind,
                               value: item.value,
                               capturedAt: ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: now)),
                               attempts: 0,
                               nextAttemptAt: nextAttemptAt,
                               lastError: nil)
        entries.append(entry)
        noteAccepted(item, now: now)
        save()
        return entry
    }

    // MARK: - 发送

    func nextReady(now: Double) -> QueueEntry? {
        entries.filter { $0.nextAttemptAt <= now }.min(by: { $0.nextAttemptAt < $1.nextAttemptAt })
    }

    func markSent(_ id: String, result: ResultRecord) {
        entries.removeAll { $0.id == id }
        append(result)
        save()
    }

    func markFailed(_ id: String, error: String, now: Double, retryIn: TimeInterval) {
        guard let idx = entries.firstIndex(where: { $0.id == id }) else { return }
        entries[idx].attempts += 1
        entries[idx].lastError = error
        entries[idx].nextAttemptAt = now + retryIn
        save()
    }

    /// 让一整个队列立刻可以重试（用户点“现在重试”）。
    func kickAll(now: Double) {
        for idx in entries.indices { entries[idx].nextAttemptAt = now }
        save()
    }

    func record(_ result: ResultRecord) {
        append(result)
        save()
    }

    private func append(_ result: ResultRecord) {
        results.append(result)
        if results.count > maxResults { results.removeFirst(results.count - maxResults) }
    }

    private func trimHistory(now: Double) {
        if history.count <= maxHistory { return }
        let sorted = history.sorted { $0.value > $1.value }
        history = Dictionary(uniqueKeysWithValues: sorted.prefix(maxHistory).map { ($0.key, $0.value) })
    }

    // MARK: - 落盘

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let state = try? JSONDecoder().decode(PersistedState.self, from: data) else { return }
        entries = state.entries
        history = state.history
        results = state.results
    }

    func save() {
        let state = PersistedState(version: 1, entries: entries, history: history, results: results)
        guard let data = try? JSONEncoder().encode(state) else { return }
        try? data.write(to: fileURL, options: [.atomic])
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
    }

    // 仅测试用：确认历史里有什么。
    func hasHistory(_ item: ClipItem) -> Bool {
        dedupeKeys(for: item).contains { history[$0] != nil }
    }
}
