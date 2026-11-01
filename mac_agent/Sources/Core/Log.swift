import Foundation

/// 极简日志：只写时间、级别和调用方给的摘要（哈希前缀 / 域名 / 结果），绝不写剪贴板全文。
final class AgentLog {
    static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    static let hhmmss: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    private let fileURL: URL?
    var url: URL? { fileURL }
    private let maxBytes: Int
    private let lock = NSLock()
    var echoToStderr: Bool

    init(dir: URL?, echoToStderr: Bool = false) {
        self.echoToStderr = echoToStderr
        if let dir {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            self.fileURL = dir.appendingPathComponent("agent.log")
        } else {
            self.fileURL = nil
        }
        self.maxBytes = 512 * 1024
    }

    func info(_ message: String) { write("INFO", message) }
    func warn(_ message: String) { write("WARN", message) }
    func error(_ message: String) { write("ERROR", message) }

    func tail(_ lines: Int) -> String {
        guard let fileURL, let text = try? String(contentsOf: fileURL, encoding: .utf8) else { return "" }
        let all = text.split(separator: "\n", omittingEmptySubsequences: true)
        return all.suffix(lines).joined(separator: "\n")
    }

    private func write(_ level: String, _ message: String) {
        let line = "\(AgentLog.iso.string(from: Date())) \(level) \(message)\n"
        if echoToStderr {
            FileHandle.standardError.write(line.data(using: .utf8) ?? Data())
        }
        guard let fileURL else { return }
        lock.lock()
        defer { lock.unlock() }
        rotateIfNeeded(fileURL)
        if let handle = try? FileHandle(forWritingTo: fileURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: line.data(using: .utf8) ?? Data())
        } else {
            try? line.data(using: .utf8)?.write(to: fileURL)
            // 队列文件/日志都是本机私密状态，只给当前用户读写。
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
        }
    }

    private func rotateIfNeeded(_ url: URL) {
        guard let size = try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber,
              size.intValue > maxBytes else { return }
        let backup = url.appendingPathExtension("1")
        try? FileManager.default.removeItem(at: backup)
        try? FileManager.default.moveItem(at: url, to: backup)
    }
}

/// 只用于日志/菜单的短摘要，避免任何地方把链接全文写出去。
enum Redact {
    static func hashPrefix(_ hex: String) -> String {
        String(hex.prefix(8)) + "…"
    }
    static func host(_ urlString: String) -> String {
        guard let url = URL(string: urlString) else { return "（无效网址）" }
        return url.host ?? "（无域名）"
    }
}
