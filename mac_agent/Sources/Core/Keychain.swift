import Foundation
import Security

/// API Key 只放在登录钥匙串里，不写文件、不进日志。
enum Keychain {
    static let service = "com.qbnas.macagent"
    static let account = "apiKey"

    enum Failure: Error {
        case status(OSStatus)
        var message: String {
            switch self {
            case .status(let code):
                let text = (SecCopyErrorMessageString(code, nil) as String?) ?? "代码 \(code)"
                return "钥匙串访问失败：\(text)"
            }
        }
    }

    static func read() -> String? {
        var query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account,
                                    kSecReturnData as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        query[kSecUseDataProtectionKeychain as String] = false
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        guard status == errSecSuccess, let data = out as? Data else { return nil }
        let value = String(data: data, encoding: .utf8) ?? ""
        return value.isEmpty ? nil : value
    }

    static func write(_ value: String) throws {
        let data = Data(value.utf8)
        var query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        query[kSecUseDataProtectionKeychain as String] = false
        if SecItemCopyMatching(query as CFDictionary, nil) == errSecSuccess {
            let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
            if status != errSecSuccess { throw Failure.status(status) }
            return
        }
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrLabel as String] = "QBNAS 剪贴板代理 · API Key"
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        let status = SecItemAdd(add as CFDictionary, nil)
        if status != errSecSuccess { throw Failure.status(status) }
    }

    static func delete() {
        var query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        query[kSecUseDataProtectionKeychain as String] = false
        SecItemDelete(query as CFDictionary)
    }

    static func hasKey() -> Bool { read() != nil }
}
