import AppKit

/// 无界面运行：与菜单栏 App 用同一份核心代码，只是不画界面。
/// 用途：自动化验收（离线排队 / 恢复补发 / 重启不重收）、排障。
enum HeadlessRunner {
    static func run(arguments: [String]) -> Never {
        let settings = Settings()
        let log = AgentLog(dir: settings.supportDir, echoToStderr: true)
        let store = QueueStore(dir: settings.supportDir)
        let client = APIClient(baseURLProvider: { settings.baseURL }, apiKeyProvider: { settings.apiKey })
        let core = AgentCore(settings: settings, store: store, client: client, log: log)
        core.attach(pasteboard: SystemPasteboard())
        core.onCapture = { item in print("capture \(item.kind.rawValue) \(item.shortLabel)") }
        core.onSnapshot = { snapshot in
            print("state online=\(snapshot.online) paused=\(snapshot.paused) key=\(snapshot.keyConfigured) queue=\(snapshot.queueCount)")
        }
        core.start()

        var duration: Double?
        if let idx = arguments.firstIndex(of: "--duration"), idx + 1 < arguments.count {
            duration = Double(arguments[idx + 1])
        }
        if let duration {
            DispatchQueue.main.asyncAfter(deadline: .now() + duration) {
                core.stop()
                exit(0)
            }
        }
        for signalNumber in [SIGTERM, SIGINT] {
            signal(signalNumber, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
            source.setEventHandler {
                core.stop()
                exit(0)
            }
            source.resume()
            signalSources.append(source)
        }
        RunLoop.main.run()
        exit(0)
    }

    nonisolated(unsafe) static var signalSources: [DispatchSourceSignal] = []
}

/// 安装逻辑自检：把 app 复制到假的 HOME 里、写 plist、再“卸载”，确认没动别的东西。
enum InstallCheck {
    static func run() -> Never {
        let paths = LaunchAgent.paths
        print("fake root: \(paths.home.path)")
        do {
            try Installer.installAppBundle()
            print("app copied: \(FileManager.default.fileExists(atPath: paths.app.path))")
            print("plist: \(FileManager.default.fileExists(atPath: paths.plist.path))")
            if FileManager.default.fileExists(atPath: paths.plist.path) {
                let data = try Data(contentsOf: paths.plist)
                let plist = try PropertyListSerialization.propertyList(from: data, options: [], format: nil)
                print("plist valid: \(String(describing: plist).count > 0)")
            }
            let script = Installer.uninstallScript(paths: paths)
            let scriptURL = paths.home.appendingPathComponent("uninstall.sh")
            try script.write(to: scriptURL, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: scriptURL.path)
            print("uninstall script written: \(scriptURL.path)")
        } catch {
            print("install failed: \(error)")
        }
        exit(0)
    }
}
