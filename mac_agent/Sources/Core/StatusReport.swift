import Foundation

/// 写一份给“自检 / 自动化验收”看的状态文件：只有状态和短摘要，没有剪贴板全文、没有 key。
enum StatusReport {
    nonisolated(unsafe) private static var lastSignature: String = ""

    static func write(snapshot: AgentSnapshot, settings: Settings) {
        let signature = "\(snapshot.online)|\(snapshot.paused)|\(snapshot.queueCount)|\(snapshot.keyConfigured)|\(snapshot.keyRejected)|\(snapshot.lastError ?? "")|\(snapshot.results.count)|\(snapshot.lastSentAt ?? 0)|\(snapshot.baseURL)"
        if signature == lastSignature { return }
        lastSignature = signature
        let dir = settings.supportDir
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let results: [[String: Any]] = snapshot.results.map {
            ["at": $0.at, "kind": $0.kind, "status": $0.status, "detail": $0.detail]
        }
        let payload: [String: Any] = [
            "updated_at": ISO8601DateFormatter().string(from: Date()),
            "pid": ProcessInfo.processInfo.processIdentifier,
            "version": snapshot.version,
            "paused": snapshot.paused,
            "online": snapshot.online,
            "key_configured": snapshot.keyConfigured,
            "key_rejected": snapshot.keyRejected,
            "key_source": settings.keySourceDescription,
            "queue_count": snapshot.queueCount,
            "base_url": snapshot.baseURL,
            "last_health_at": snapshot.lastHealthAt ?? 0,
            "last_sent_at": snapshot.lastSentAt ?? 0,
            "last_capture_label": snapshot.lastCaptureLabel ?? "",
            "last_error": snapshot.lastError ?? "",
            "results": results,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else { return }
        let file = dir.appendingPathComponent("status.json")
        try? data.write(to: file, options: [.atomic])
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }
}
