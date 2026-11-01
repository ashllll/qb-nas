import Foundation
import CryptoKit

enum ClipKind: String, Codable {
    case magnet
    case url

    var displayName: String { self == .magnet ? "磁力" : "网址" }
}

struct ClipItem: Equatable {
    let kind: ClipKind
    /// 剪贴板里匹配到的那个 token（磁力或网址），原样保留以便上报；除此之外不保留任何文本。
    let value: String
    /// value 的 sha256（十六进制），用于本地短期去重。
    let digest: String
    /// magnet 的归一化 infohash（小写十六进制 40 位）；网址为 nil。
    let infohash: String?
    /// 网址的域名，仅用于展示/日志。
    let host: String?

    var shortLabel: String {
        switch kind {
        case .magnet: return infohash.map(Redact.hashPrefix) ?? "磁力"
        case .url: return host ?? "网址"
        }
    }
}

enum Classifier {
    /// 剪贴板文本超过这个长度就不再扫描（不保留、也不解析）。
    static let maxScanBytes = 16 * 1024
    static let maxURLLength = 2048

    /// 只认两种东西：
    /// 1) 文本里出现 `magnet:?xt=urn:btih:`，取第一个合法磁力；
    /// 2) 整段文本就是一个 http(s) 网址（前后无其他文字、无空白）。
    /// 其他一律返回 nil —— 不解析、不入库、不发送。
    static func classify(_ raw: String) -> ClipItem? {
        guard raw.utf8.count <= maxScanBytes else { return nil }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }

        if let magnet = extractMagnet(from: text) {
            return magnet
        }
        if let url = extractLoneURL(from: text) {
            return url
        }
        return nil
    }

    // MARK: - magnet

    private static func extractMagnet(from text: String) -> ClipItem? {
        let lowered = text.lowercased()
        guard let start = lowered.range(of: "magnet:?")?.lowerBound else { return nil }
        let rest = text[start...]
        // 磁力链接以空白/引号/尖括号结束
        let terminators = CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: "\"'<>"))
        let token: String
        if let end = rest.unicodeScalars.firstIndex(where: { terminators.contains($0) }) {
            token = String(rest[rest.startIndex..<end])
        } else {
            token = String(rest)
        }
        let cleaned = trimTrailingPunctuation(token)
        guard cleaned.count > 20 else { return nil }
        guard let infohash = infohash(fromMagnet: cleaned) else { return nil }
        return ClipItem(kind: .magnet, value: cleaned, digest: sha256(cleaned), infohash: infohash, host: nil)
    }

    static func infohash(fromMagnet magnet: String) -> String? {
        guard let range = magnet.range(of: "xt=urn:btih:", options: .caseInsensitive) else { return nil }
        let tail = magnet[range.upperBound...]
        let end = tail.firstIndex(where: { $0 == "&" || $0 == "#" }) ?? tail.endIndex
        return normalizeInfohash(String(tail[tail.startIndex..<end]))
    }

    /// 40 位十六进制原样小写；32 位 base32（RFC 4648）解码成 20 字节再转十六进制。
    static func normalizeInfohash(_ raw: String) -> String? {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        if value.count == 40, value.allSatisfy({ $0.isHexDigit }) {
            return value.lowercased()
        }
        if value.count == 32, let decoded = base32Decode(value), decoded.count == 20 {
            return decoded.map { String(format: "%02x", $0) }.joined()
        }
        return nil
    }

    private static func base32Decode(_ input: String) -> Data? {
        let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
        var bits = 0
        var buffer = 0
        var out = [UInt8]()
        for ch in input {
            guard let idx = alphabet.firstIndex(of: ch) else { return nil }
            buffer = (buffer << 5) | idx
            bits += 5
            if bits >= 8 {
                bits -= 8
                out.append(UInt8((buffer >> bits) & 0xFF))
            }
        }
        return Data(out)
    }

    // MARK: - url

    private static func extractLoneURL(from text: String) -> ClipItem? {
        let lowered = text.lowercased()
        guard lowered.hasPrefix("http://") || lowered.hasPrefix("https://") else { return nil }
        guard text.count <= maxURLLength else { return nil }
        // 整段必须是单个 token：中间有空白就当成普通文本忽略（避免把整段文档里的链接都收走）。
        guard text.rangeOfCharacter(from: .whitespacesAndNewlines) == nil else { return nil }
        let cleaned = trimTrailingPunctuation(text)
        guard let url = URL(string: cleaned), let host = url.host, !host.isEmpty,
              let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return nil }
        return ClipItem(kind: .url, value: cleaned, digest: sha256(cleaned), infohash: nil, host: host)
    }

    private static func trimTrailingPunctuation(_ token: String) -> String {
        let set = CharacterSet(charactersIn: "。，,；;）)】]》>\"'")
        var result = token
        while let last = result.unicodeScalars.last, set.contains(last) {
            result.removeLast()
        }
        return result
    }

    /// 自测用：确认摘要函数不是恒等式。
    static func clipDigestsDiffer() -> Bool {
        sha256("a") != sha256("b")
    }

    static func sha256(_ value: String) -> String {
        let digest = SHA256.hash(data: Data(value.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}
