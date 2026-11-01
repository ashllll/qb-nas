import SwiftUI
import AppKit

final class SettingsModel: ObservableObject {
    @Published var address: String
    @Published var key: String = ""
    @Published var message: String = ""
    @Published var messageIsError = false
    @Published var testing = false
    @Published var launchAtLogin: Bool
    @Published var keySource: String

    let installed: Bool
    let supportPath: String
    let version: String
    private let settings: Settings
    var onSaved: (() -> Void)?
    var onToggleLogin: ((Bool) -> Bool)?

    init(settings: Settings, launchAtLogin: Bool, installed: Bool) {
        self.settings = settings
        self.address = settings.baseURLString
        self.launchAtLogin = launchAtLogin
        self.installed = installed
        self.keySource = settings.keySourceDescription
        self.supportPath = settings.supportDir.path
        self.version = AgentVersion.string
    }

    func reloadFromDisk() {
        address = settings.baseURLString
        keySource = settings.keySourceDescription
        launchAtLogin = LaunchAgent.isInstalled() && LaunchAgent.isLoaded()
    }

    func save() {
        let trimmedAddress = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedAddress.isEmpty else {
            message = "地址不能为空"
            messageIsError = true
            return
        }
        settings.setBaseURL(trimmedAddress)
        if !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            do {
                try settings.storeAPIKey(key)
                key = ""
                message = "已保存。API Key 存进钥匙串了。"
                messageIsError = false
            } catch {
                message = "地址已保存，但 API Key 没写进钥匙串：\(error)"
                messageIsError = true
            }
        } else {
            message = "已保存。"
            messageIsError = false
        }
        keySource = settings.keySourceDescription
        onSaved?()
    }

    func testConnection() {
        testing = true
        message = "正在连接…"
        messageIsError = false
        let address = self.address
        let typedKey = key.trimmingCharacters(in: .whitespacesAndNewlines)
        let storedKey = settings.apiKey
        Task {
            var urlString = address.trimmingCharacters(in: .whitespacesAndNewlines)
            if !urlString.lowercased().hasPrefix("http") { urlString = "http://" + urlString }
            let client = APIClient(baseURLProvider: { URL(string: urlString) },
                                   apiKeyProvider: { typedKey.isEmpty ? storedKey : typedKey })
            do {
                let info = try await client.health()
                var text = "连接成功 · NAS \(info.version ?? "版本未报")"
                if let qbit = info.qbitOK { text += " · qB \(qbit ? "在" : "没连上")" }
                if let crawler = info.crawlerOK { text += " · 取页 \(crawler ? "就绪" : "未就绪")" }
                await MainActor.run {
                    self.message = text
                    self.messageIsError = false
                    self.testing = false
                }
            } catch {
                let text = (error as? APIError)?.shortDescription ?? error.localizedDescription
                await MainActor.run {
                    self.message = "连不上：\(text)"
                    self.messageIsError = true
                    self.testing = false
                }
            }
        }
    }

    func setLaunchAtLogin(_ want: Bool) {
        guard let onToggleLogin else { return }
        let actual = onToggleLogin(want)
        launchAtLogin = actual
        if want && !actual {
            message = "没能写入登录项"
            messageIsError = true
        }
    }

    func clearKey() {
        settings.clearAPIKey()
        keySource = settings.keySourceDescription
        message = "钥匙串里的 Key 已删除。"
        messageIsError = false
        onSaved?()
    }

    func openNASPage() {
        var urlString = address.trimmingCharacters(in: .whitespacesAndNewlines)
        if !urlString.lowercased().hasPrefix("http") { urlString = "http://" + urlString }
        if let url = URL(string: urlString) { NSWorkspace.shared.open(url) }
    }
}

struct SettingsView: View {
    @ObservedObject var model: SettingsModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("连接 NAS")
                .font(.headline)
            Text("NAS 上跑着常驻采集服务。这里只填地址和 API Key，代理自己会排队、补发。")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 6) {
                Text("地址").font(.caption).foregroundStyle(.secondary)
                TextField("http://192.168.20.193:8899", text: $model.address)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("API Key").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Text("来源：\(model.keySource)").font(.caption2).foregroundStyle(.tertiary)
                }
                SecureField("留空表示不修改", text: $model.key)
                    .textFieldStyle(.roundedBorder)
                HStack(spacing: 10) {
                    Button("清除钥匙串里的 Key") { model.clearKey() }
                        .buttonStyle(.link)
                        .font(.caption)
                    Spacer()
                }
            }

            Toggle("登录时启动（只加这一个登录项）", isOn: Binding(
                get: { model.launchAtLogin },
                set: { model.setLaunchAtLogin($0) }))
                .disabled(!model.installed)

            HStack(spacing: 10) {
                Button(model.testing ? "正在测试…" : "测试连接") { model.testConnection() }
                    .disabled(model.testing)
                Button("保存") { model.save() }
                    .keyboardShortcut(.defaultAction)
                Button("打开 NAS 页面") { model.openNASPage() }
                Spacer()
            }

            if !model.message.isEmpty {
                Text(model.message)
                    .font(.callout)
                    .foregroundStyle(model.messageIsError ? Color.red : Color.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "lock.shield")
                    .foregroundStyle(.tertiary)
                Text("只读剪贴板文本；不读浏览器和窗口标题，不要辅助功能权限；剪贴板全文不落盘，日志里只有哈希前缀和域名。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            Text("版本 \(model.version) · 数据目录 \(model.supportPath)")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .padding(20)
        .frame(width: 460, height: 430)
    }
}
