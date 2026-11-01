import SwiftUI
import AppKit

final class SelfCheckModel: ObservableObject {
    @Published var lines: [(String, String)] = []
    @Published var logTail: String = ""
    @Published var copied = false

    private let snapshotProvider: () -> AgentSnapshot
    private let settings: Settings
    private let log: AgentLog

    init(snapshot: AgentSnapshot, settings: Settings, log: AgentLog) {
        self.snapshotProvider = { snapshot }
        self.settings = settings
        self.log = log
        reload()
    }

    init(snapshotProvider: @escaping () -> AgentSnapshot, settings: Settings, log: AgentLog) {
        self.snapshotProvider = snapshotProvider
        self.settings = settings
        self.log = log
        reload()
    }

    func reload() {
        let s = snapshotProvider()
        let keychainWorking = keychainUsable()
        lines = [
            ("版本", "\(s.version) · \(Installer.currentBundle.path)"),
            ("地址", s.baseURL),
            ("NAS", s.online ? "在线" : "不在线\(s.lastError.map { "（\($0)）" } ?? "")"),
            ("队列", "\(s.queueCount) 条待发"),
            ("API Key", "\(settings.keySourceDescription)\(keychainWorking ? "" : " · 钥匙串当前不可用")"),
            ("登录项", LaunchAgent.isInstalled()
                ? (LaunchAgent.isLoaded() ? "已安装并已加载" : "已安装，未加载")
                : "未安装"),
            ("已装到", Installer.isInstalled ? LaunchAgent.paths.app.path : "还在临时位置（未安装）"),
            ("数据目录", settings.supportDir.path),
            ("日志", log.url?.path ?? "未写日志"),
        ]
        logTail = log.tail(40)
        copied = false
    }

    private func keychainUsable() -> Bool {
        // 环境变量提供 key 时不算钥匙串可用性，只判断实际读写是否成功。
        Keychain.read() != nil || Keychain.hasKey()
    }

    func openLogFolder() {
        if let url = log.url { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }

    func copyDiagnostics() {
        let text = lines.map { "\($0.0)：\($0.1)" }.joined(separator: "\n") + "\n\n--- 日志 ---\n" + logTail
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
        copied = true
    }
}

struct SelfCheckView: View {
    @ObservedObject var model: SelfCheckModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("自检").font(.headline)
                Spacer()
                Button("重新检查") { model.reload() }
                Button("打开日志") { model.openLogFolder() }
                Button(model.copied ? "已复制" : "复制诊断") { model.copyDiagnostics() }
            }
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(model.lines.enumerated()), id: \.offset) { _, pair in
                    HStack(alignment: .top, spacing: 8) {
                        Text(pair.0)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(width: 64, alignment: .leading)
                        Text(pair.1)
                            .font(.caption)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            Divider()
            ScrollView {
                Text(model.logTail.isEmpty ? "（还没有日志）" : model.logTail)
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: 200)
            .background(Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
        .padding(20)
        .frame(minWidth: 560, minHeight: 460)
    }
}

final class InstallViewModel: ObservableObject {
    let installed: Bool
    let targetPath: String
    var onInstall: (() -> Void)?
    var onLater: (() -> Void)?

    init(installed: Bool, targetPath: String, onInstall: (() -> Void)?, onLater: (() -> Void)?) {
        self.installed = installed
        self.targetPath = targetPath
        self.onInstall = onInstall
        self.onLater = onLater
    }
}

struct InstallView: View {
    @ObservedObject var model: InstallViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(model.installed ? "只差一个登录项" : "把这只小眼睛装到这台 Mac 上")
                .font(.title3).bold()
            Text(model.installed
                 ? "app 已经在「应用程序」里了。这里只加一个登录项，让它在登录时自动开始工作；不改任何其它系统设置。"
                 : """
            安装会做两件事：把 app 放进「应用程序」，再加一个登录项，让它在登录时自动开始工作。除此之外不改任何系统设置，也不需要辅助功能、浏览器扩展或终端。
            安装后菜单栏会出现一只小剪贴板：绿点=NAS 在线，红点=NAS 不在线（会先排队），灰点=已暂停。
            """)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("目标位置：\(model.targetPath)")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .textSelection(.enabled)
            HStack(spacing: 10) {
                Button(model.installed ? "加上登录启动" : "安装并设为登录启动") { model.onInstall?() }
                    .keyboardShortcut(.defaultAction)
                Button("先不安装") { model.onLater?() }
                Spacer()
            }
        }
        .padding(22)
        .frame(width: 480)
    }
}
