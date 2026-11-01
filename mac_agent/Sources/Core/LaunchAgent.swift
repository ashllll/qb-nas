import Foundation

/// 只管理一个登录项：com.qbnas.macagent。不碰用户已有的其它 LaunchAgents。
struct LaunchAgent {
    static let label = "com.qbnas.macagent"
    static let fileName = "com.qbnas.macagent.plist"

    struct Paths {
        let home: URL
        init(home: URL = URL(fileURLWithPath: NSHomeDirectory())) { self.home = home }
        var agentsDir: URL { home.appendingPathComponent("Library/LaunchAgents", isDirectory: true) }
        var plist: URL { agentsDir.appendingPathComponent(LaunchAgent.fileName) }
        var appsDir: URL { home.appendingPathComponent("Applications", isDirectory: true) }
        var app: URL { appsDir.appendingPathComponent("NAS 剪贴板代理.app", isDirectory: true) }
        var binary: URL { app.appendingPathComponent("Contents/MacOS/QBNASAgent") }
        var supportDir: URL {
            home.appendingPathComponent("Library/Application Support/QBNASMacAgent", isDirectory: true)
        }
    }

    /// 测试可以指定一个假 HOME，并关掉真正调用 launchctl 的动作。
    static var paths: Paths {
        let env = ProcessInfo.processInfo.environment
        if let root = env["QBNAS_INSTALL_ROOT"], !root.isEmpty {
            return Paths(home: URL(fileURLWithPath: root, isDirectory: true))
        }
        return Paths()
    }

    static var usesFakeRoot: Bool {
        let env = ProcessInfo.processInfo.environment
        return !(env["QBNAS_INSTALL_ROOT"] ?? "").isEmpty
    }

    static func plistObject(binaryPath: String) -> [String: Any] {
        [
            "Label": label,
            "ProgramArguments": [binaryPath],
            "RunAtLoad": true,
            // 崩溃（非 0 退出）会被拉起；菜单里“退出”用正常退出，不会被拉起。
            "KeepAlive": ["SuccessfulExit": false],
            "ThrottleInterval": 10,
            "ProcessType": "Interactive",
            "LimitLoadToSessionType": "Aqua",
            "StandardOutPath": paths.supportDir.appendingPathComponent("launchd.out.log").path,
            "StandardErrorPath": paths.supportDir.appendingPathComponent("launchd.err.log").path,
        ]
    }

    /// 登录项应该指向哪个可执行文件：假 HOME 测试时用假路径，真机用当前这份 app 的路径。
    static var targetBinaryPath: String {
        if usesFakeRoot { return paths.binary.path }
        return Bundle.main.executableURL?.path ?? paths.binary.path
    }

    static func plistData(binaryPath: String) -> Data? {
        try? PropertyListSerialization.data(fromPropertyList: plistObject(binaryPath: binaryPath),
                                            format: .xml, options: 0)
    }

    static func isInstalled() -> Bool {
        FileManager.default.fileExists(atPath: paths.plist.path)
    }

    static func isLoaded() -> Bool {
        guard !usesFakeRoot else { return isInstalled() }
        let result = run("/bin/launchctl", ["print", "gui/\(getuid())/\(label)"])
        return result == 0
    }

    static func plistMatchesBinary() -> Bool {
        guard let data = try? Data(contentsOf: paths.plist),
              let obj = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any],
              let args = obj["ProgramArguments"] as? [String], let first = args.first else { return false }
        return first == paths.binary.path
    }

    @discardableResult
    static func install() -> Bool {
        let paths = self.paths
        do {
            try FileManager.default.createDirectory(at: paths.agentsDir, withIntermediateDirectories: true)
            try FileManager.default.createDirectory(at: paths.supportDir, withIntermediateDirectories: true)
            guard let data = plistData(binaryPath: targetBinaryPath) else { return false }
            try data.write(to: paths.plist, options: [.atomic])
        } catch {
            return false
        }
        guard !usesFakeRoot else { return true }
        _ = run("/bin/launchctl", ["bootout", "gui/\(getuid())/\(label)"])
        let status = run("/bin/launchctl", ["bootstrap", "gui/\(getuid())", paths.plist.path])
        if status != 0 {
            // 老系统兜底
            return run("/bin/launchctl", ["load", "-w", paths.plist.path]) == 0
        }
        return true
    }

    static func uninstall() {
        let paths = self.paths
        if !usesFakeRoot { _ = run("/bin/launchctl", ["bootout", "gui/\(getuid())/\(label)"]) }
        try? FileManager.default.removeItem(at: paths.plist)
    }

    @discardableResult
    static func run(_ launchPath: String, _ args: [String]) -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launchPath)
        process.arguments = args
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus
        } catch {
            return -1
        }
    }
}
