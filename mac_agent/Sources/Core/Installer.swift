import Foundation

/// 安装 / 卸载：只动三处——~/Applications 里的 app、一个 LaunchAgent plist、以及自己的支持目录与钥匙串条目。
enum Installer {
    static var paths: LaunchAgent.Paths { LaunchAgent.paths }

    static var currentBundle: URL { Bundle.main.bundleURL }

    /// 已经装在「应用程序」里了（~/Applications 或 /Applications 都算）。
    static var isInstalled: Bool {
        let path = currentBundle.standardizedFileURL.path
        let standard = paths.app.standardizedFileURL.path
        return path == standard || path.hasPrefix("/Applications/NAS 剪贴板代理.app")
    }

    static var installTargetDescription: String { paths.app.path }

    enum InstallError: Error {
        case copyFailed(String)
        case plistFailed
        case launchctlFailed
    }

    /// 把当前 .app 复制到 ~/Applications，写入登录项并加载。返回后调用方负责用新路径重新打开并退出自己。
    static func installAppBundle(quiet: Bool = false) throws {
        let source = currentBundle
        let target = paths.app
        if isInstalled {
            // 已经在 /Applications 里了，只补登录项，不再复制第二份。
            guard LaunchAgent.install() else { throw InstallError.plistFailed }
            return
        }
        if source.standardizedFileURL.path == target.standardizedFileURL.path {
            return
        }
        let fm = FileManager.default
        try fm.createDirectory(at: paths.appsDir, withIntermediateDirectories: true)
        try fm.createDirectory(at: paths.supportDir, withIntermediateDirectories: true)
        if fm.fileExists(atPath: target.path) {
            try? fm.removeItem(at: target)
        }
        do {
            try fm.copyItem(at: source, to: target)
        } catch {
            throw InstallError.copyFailed(error.localizedDescription)
        }
        guard LaunchAgent.install() else { throw InstallError.plistFailed }
    }

    /// 卸载脚本：等主进程退出后清掉登录项、plist、app 本体和支持目录。
    static func uninstallScript(paths: LaunchAgent.Paths, uid: UInt32 = getuid()) -> String {
        let label = LaunchAgent.label
        return """
        #!/bin/sh
        # QBNAS 剪贴板代理 · 卸载
        sleep 1
        /bin/launchctl bootout gui/\(uid)/\(label) 2>/dev/null
        /bin/rm -f '\(paths.plist.path)'
        /bin/rm -rf '\(paths.app.path)'
        /bin/rm -rf '\(paths.supportDir.path)'
        /bin/rm -f "$0"
        """
    }

    /// 真正卸载：先删钥匙串条目（需要本进程权限），再交给后台脚本清理文件。
    static func uninstall() {
        Keychain.delete()
        let script = uninstallScript(paths: paths)
        let scriptURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("qbnas-uninstall-\(UUID().uuidString).sh")
        do {
            try script.write(to: scriptURL, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: scriptURL.path)
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/sh")
            process.arguments = [scriptURL.path]
            try process.run()
        } catch {
            // 脚本起不来时至少把登录项摘掉，避免留下半装状态。
            LaunchAgent.uninstall()
        }
    }
}
