import Foundation

/// 配置：地址、暂停状态放 UserDefaults；API Key 只放钥匙串。
/// 支持环境变量覆盖，仅用于自动化验收与排障，正常双击启动不会用到。
struct Settings {
    static let defaultBaseURL = "http://192.168.20.193:8899"

    let defaults: UserDefaults
    let environment: [String: String]

    init(defaults: UserDefaults = .standard, environment: [String: String] = ProcessInfo.processInfo.environment) {
        self.defaults = defaults
        self.environment = environment
    }

    // MARK: - 支持目录

    var supportDir: URL {
        if let override = environment["QBNAS_AGENT_HOME"], !override.isEmpty {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
        return base.appendingPathComponent("QBNASMacAgent", isDirectory: true)
    }

    // MARK: - 地址

    var baseURLString: String {
        if let override = environment["QBNAS_BASE_URL"], !override.isEmpty { return override }
        return defaults.string(forKey: "baseURL") ?? Settings.defaultBaseURL
    }

    var baseURL: URL? {
        var text = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.lowercased().hasPrefix("http://") && !text.lowercased().hasPrefix("https://") {
            text = "http://" + text
        }
        guard let url = URL(string: text), url.host != nil else { return nil }
        return url
    }

    func setBaseURL(_ value: String) {
        defaults.set(value, forKey: "baseURL")
    }

    // MARK: - 暂停

    var paused: Bool {
        get {
            if let override = environment["QBNAS_PAUSED"], !override.isEmpty {
                return override == "1" || override.lowercased() == "true"
            }
            return defaults.bool(forKey: "paused")
        }
        nonmutating set { defaults.set(newValue, forKey: "paused") }
    }

    // MARK: - Key

    /// 来源优先级：显式环境变量（测试/应急）> 钥匙串。
    var apiKey: String? {
        if let override = environment["QBNAS_API_KEY"], !override.isEmpty { return override }
        return Keychain.read()
    }

    var keySourceDescription: String {
        if let override = environment["QBNAS_API_KEY"], !override.isEmpty { return "环境变量（仅验收用）" }
        return Keychain.hasKey() ? "钥匙串" : "未设置"
    }

    func storeAPIKey(_ value: String) throws {
        try Keychain.write(value.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func clearAPIKey() {
        Keychain.delete()
    }

    var version: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "1.0.0"
    }
}
