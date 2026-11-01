import Foundation

struct HealthInfo: Equatable {
    var ok: Bool
    var version: String?
    var qbitOK: Bool?
    var crawlerOK: Bool?
}

struct IngestResponse: Equatable {
    var ok: Bool
    var status: String
    var reason: String?
    var itemID: String?
    var jobID: String?
}

enum APIError: Error {
    case offline(String)          // 连不上（DNS/拒绝连接/超时）
    case unauthorized             // 401/403
    case http(Int, String)        // 其它非 2xx
    case badResponse(String)

    var isConnectivity: Bool {
        if case .offline = self { return true }
        return false
    }
    var shortDescription: String {
        switch self {
        case .offline(let m): return "连不上 NAS（\(m)）"
        case .unauthorized: return "API Key 被拒绝"
        case .http(let code, let body):
            let snippet = body.replacingOccurrences(of: "\n", with: " ").prefix(80)
            return "NAS 返回 HTTP \(code)\(snippet.isEmpty ? "" : "：\(snippet)")"
        case .badResponse(let m): return "NAS 返回无法解析（\(m)）"
        }
    }
}

/// 只用两个端点：GET /api/v1/health 看在线，POST /api/v1/ingest 交东西。
/// 兼容旧的 /api/health（p1 还没上新接口时菜单仍能显示在线）。
final class APIClient {
    var baseURLProvider: () -> URL?
    var apiKeyProvider: () -> String?

    private let session: URLSession

    init(baseURLProvider: @escaping () -> URL?, apiKeyProvider: @escaping () -> String?) {
        self.baseURLProvider = baseURLProvider
        self.apiKeyProvider = apiKeyProvider
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 20
        config.timeoutIntervalForResource = 40
        config.waitsForConnectivity = false
        config.httpAdditionalHeaders = ["User-Agent": "QBNASMacAgent/\(AgentVersion.string)"]
        self.session = URLSession(configuration: config)
    }

    private func request(path: String, method: String = "GET", body: Data? = nil) throws -> URLRequest {
        guard let base = baseURLProvider() else { throw APIError.offline("NAS 地址无效") }
        var url = base
        if !path.isEmpty { url = base.appendingPathComponent(path) }
        var req = URLRequest(url: url)
        req.httpMethod = method
        if let key = apiKeyProvider(), !key.isEmpty {
            req.setValue(key, forHTTPHeaderField: "X-API-Key")
        }
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    func health() async throws -> HealthInfo {
        var lastError: APIError = .offline("未知错误")
        for path in ["api/v1/health", "api/health"] {
            do {
                let (data, response) = try await session.data(for: try request(path: path))
                guard let http = response as? HTTPURLResponse else { throw APIError.badResponse("没有 HTTP 响应") }
                if http.statusCode == 401 || http.statusCode == 403 { throw APIError.unauthorized }
                guard (200..<300).contains(http.statusCode) else {
                    throw APIError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
                }
                var info = HealthInfo(ok: true, version: nil, qbitOK: nil, crawlerOK: nil)
                if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    if let ok = obj["ok"] as? Bool { info.ok = ok }
                    info.version = obj["version"] as? String
                    if let qbit = obj["qbit"] as? [String: Any] { info.qbitOK = qbit["ok"] as? Bool }
                    if let crawler = obj["crawler"] as? [String: Any] { info.crawlerOK = crawler["ok"] as? Bool }
                }
                if info.ok { return info }
                throw APIError.badResponse("health ok=false")
            } catch let error as APIError {
                lastError = error
                if case .unauthorized = error { throw error }
            } catch {
                lastError = .offline(describe(error))
            }
        }
        throw lastError
    }

    func ingest(_ entry: QueueEntry) async throws -> IngestResponse {
        let payload: [String: Any] = [
            "event_id": entry.id,
            "kind": entry.kind.rawValue,
            "value": entry.value,
            "captured_at": entry.capturedAt,
        ]
        let body = try JSONSerialization.data(withJSONObject: payload, options: [])
        let (data, response) = try await session.data(for: try request(path: "api/v1/ingest", method: "POST", body: body))
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse("没有 HTTP 响应") }
        let text = String(data: data, encoding: .utf8) ?? ""
        if http.statusCode == 401 || http.statusCode == 403 { throw APIError.unauthorized }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(http.statusCode, text)
        }
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw APIError.badResponse("响应不是 JSON")
        }
        let ok = (obj["ok"] as? Bool) ?? ((obj["status"] as? String) != nil)
        let status = (obj["status"] as? String) ?? (ok ? "accepted" : "rejected")
        return IngestResponse(ok: ok,
                              status: status,
                              reason: (obj["reason"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                              itemID: obj["item_id"] as? String,
                              jobID: obj["job_id"] as? String)
    }

    private func describe(_ error: Error) -> String {
        if let urlError = error as? URLError {
            switch urlError.code {
            case .cannotConnectToHost, .cannotFindHost, .networkConnectionLost: return "服务没开或地址不对"
            case .timedOut: return "超时"
            case .notConnectedToInternet: return "网络断开"
            case .appTransportSecurityRequiresSecureConnection: return "被 ATS 拦下"
            default: return urlError.localizedDescription
            }
        }
        return error.localizedDescription
    }
}

enum AgentVersion {
    static var string: String {
        let short = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "1.0.0"
        let build = (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "1"
        return "\(short)(\(build))"
    }
}
