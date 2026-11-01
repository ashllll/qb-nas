import SwiftUI
import AppKit

// 沙箱里截不了屏，就用同一份 SwiftUI 视图放进离屏窗口渲染成 PNG，
// 既是给用户的预览，也顺手验证窗口布局没有溢出。

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let outDir = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "build")
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

func capture<V: View>(_ view: V, size: NSSize, name: String, scale: CGFloat = 2) {
    let hosting = NSHostingController(rootView: view)
    let window = NSWindow(contentViewController: hosting)
    window.styleMask = [.titled]
    window.setContentSize(size)
    window.setFrameOrigin(NSPoint(x: -10000, y: -10000))
    window.orderFrontRegardless()
    RunLoop.main.run(until: Date().addingTimeInterval(0.8))
    guard let content = window.contentView else { return }
    let bounds = content.bounds
    guard let rep = content.bitmapImageRepForCachingDisplay(in: bounds) else { return }
    content.cacheDisplay(in: bounds, to: rep)
    if let png = rep.representation(using: .png, properties: [:]) {
        let url = outDir.appendingPathComponent("preview-\(name).png")
        try? png.write(to: url)
        print("wrote \(url.path) \(rep.pixelsWide)x\(rep.pixelsHigh)")
    }
    window.orderOut(nil)
}

func menuBarStrip() {
    let size = NSSize(width: 260, height: 30)
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil,
                                     pixelsWide: Int(size.width * 2), pixelsHigh: Int(size.height * 2),
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return }
    rep.size = size
    let ctx = NSGraphicsContext(bitmapImageRep: rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx
    let appearance = NSAppearance(named: .darkAqua)
    appearance?.performAsCurrentDrawingAppearance {
        NSColor(calibratedWhite: 0.14, alpha: 1).setFill()
        NSRect(origin: .zero, size: size).fill()
        NSColor(calibratedWhite: 0.28, alpha: 1).setFill()
        NSRect(x: 0, y: 0, width: size.width, height: 0.5).fill()
        let states: [StatusIcon.Visual] = [.online, .offline, .paused, .keyMissing]
        var x: CGFloat = 14
        for visual in states {
            StatusIcon.image(for: visual).draw(in: NSRect(x: x, y: 4, width: 22, height: 22))
            x += 60
        }
    }
    NSGraphicsContext.restoreGraphicsState()
    if let png = rep.representation(using: .png, properties: [:]) {
        try? png.write(to: outDir.appendingPathComponent("preview-menubar.png"))
        print("wrote preview-menubar.png")
    }
}

let settings = Settings()
let settingsModel = SettingsModel(settings: settings, launchAtLogin: true, installed: true)
settingsModel.address = "http://192.168.20.193:8899"
settingsModel.keySource = "钥匙串"
settingsModel.message = "连接成功 · NAS 1.0 · qB 没连上 · 取页就绪"
capture(SettingsView(model: settingsModel), size: NSSize(width: 460, height: 430), name: "settings")

capture(InstallView(model: InstallViewModel(installed: false,
                                            targetPath: "/Applications/NAS 剪贴板代理.app",
                                            onInstall: {}, onLater: {})),
        size: NSSize(width: 480, height: 300), name: "install")

var snapshot = AgentSnapshot()
snapshot.online = true
snapshot.keyConfigured = true
snapshot.queueCount = 2
snapshot.baseURL = "http://192.168.20.193:8899"
snapshot.results = [
    ResultRecord(at: Date().timeIntervalSince1970, kind: "magnet", status: "accepted", detail: "已入库 · 8f3c1a2b…"),
    ResultRecord(at: Date().timeIntervalSince1970 - 40, kind: "url", status: "accepted", detail: "已交给 NAS 检查 · nyaa.si"),
    ResultRecord(at: Date().timeIntervalSince1970 - 120, kind: "url", status: "rejected", detail: "被拒收 · unsafe_url: 不是公开页"),
]

let selfCheck = SelfCheckModel(snapshot: snapshot, settings: settings, log: AgentLog(dir: nil))
selfCheck.lines = [
    ("版本", "1.0.0(1) · /Applications/NAS 剪贴板代理.app"),
    ("地址", "http://192.168.20.193:8899"),
    ("NAS", "在线"),
    ("队列", "2 条待发"),
    ("API Key", "钥匙串"),
    ("登录项", "已安装并已加载"),
    ("已装到", "/Applications/NAS 剪贴板代理.app"),
    ("数据目录", "~/Library/Application Support/QBNASMacAgent"),
    ("日志", "agent.log"),
]
selfCheck.logTail = """
2026-11-01T06:32:48Z INFO 收下 magnet 8f3c1a2b… · 队列 1
2026-11-01T06:32:48Z INFO 上报成功 magnet 8f3c1a2b… → accepted
2026-11-01T06:32:49Z INFO 收下 url nyaa.si · 队列 1
2026-11-01T06:32:49Z INFO 上报成功 url nyaa.si → accepted
2026-11-01T06:33:02Z WARN NAS 不在线：连不上 NAS（服务没开或地址不对）
"""
capture(SelfCheckView(model: selfCheck), size: NSSize(width: 560, height: 460), name: "selfcheck")

menuBarStrip()
print("done")
