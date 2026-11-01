import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var settings: Settings!
    private var log: AgentLog!
    private var store: QueueStore!
    private var client: APIClient!
    private var core: AgentCore!
    private var statusItem: NSStatusItem!

    private var settingsWindow: NSWindow?
    private var selfCheckWindow: NSWindow?
    private var installWindow: NSWindow?
    private var snapshot = AgentSnapshot()

    // MARK: - 生命周期

    func applicationDidFinishLaunching(_ notification: Notification) {
        settings = Settings()
        log = AgentLog(dir: settings.supportDir)
        store = QueueStore(dir: settings.supportDir)
        client = APIClient(baseURLProvider: { [weak self] in self?.settings.baseURL },
                           apiKeyProvider: { [weak self] in self?.settings.apiKey })
        core = AgentCore(settings: settings, store: store, client: client, log: log)
        core.attach(pasteboard: SystemPasteboard())
        core.onSnapshot = { [weak self] snapshot in self?.apply(snapshot) }
        core.onCapture = { [weak self] item in self?.flashCapture(item) }

        handOffToInstalledCopyIfNeeded()
        setUpStatusItem()
        core.start()
        log.info("UI 启动 · 已安装=\(Installer.isInstalled) · 登录项=\(LaunchAgent.isInstalled()) · key=\(settings.keySourceDescription)")

        if !Installer.isInstalled {
            showInstallPrompt()
        } else if settings.apiKey == nil, !UserDefaults.standard.bool(forKey: "didPromptKey") {
            UserDefaults.standard.set(true, forKey: "didPromptKey")
            showSettings()
        }
    }

    /// 避免两份代理同时读剪贴板：如果已安装的那份正在跑，就把当前这份关掉。
    private func handOffToInstalledCopyIfNeeded() {
        let running = NSRunningApplication.runningApplications(withBundleIdentifier: "com.qbnas.macagent")
            .filter { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }
        guard !running.isEmpty, !Installer.isInstalled else { return }
        if FileManager.default.fileExists(atPath: Installer.paths.app.path) {
            NSWorkspace.shared.open(Installer.paths.app)
        }
        exit(0)
    }

    func applicationWillTerminate(_ notification: Notification) {
        core?.stop()
    }

    // MARK: - 菜单栏

    private func setUpStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = StatusIcon.image(for: .offline)
        statusItem.button?.imagePosition = .imageOnly
        statusItem.button?.toolTip = "QBNAS 剪贴板代理"
        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
    }

    private func apply(_ snapshot: AgentSnapshot) {
        self.snapshot = snapshot
        statusItem.button?.image = StatusIcon.image(for: visual(for: snapshot))
        statusItem.button?.toolTip = "QBNAS · \(MenuBuilder.statusText(snapshot))"
    }

    private func visual(for snapshot: AgentSnapshot) -> StatusIcon.Visual {
        if snapshot.keyRejected { return .keyRejected }
        if !snapshot.keyConfigured { return .keyMissing }
        if snapshot.paused { return .paused }
        return snapshot.online ? .online : .offline
    }

    private func flashCapture(_ item: ClipItem) {
        statusItem.button?.image = StatusIcon.image(for: .online)
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let sections = MenuBuilder.build(snapshot: snapshot,
                                         launchAtLogin: LaunchAgent.isInstalled() && LaunchAgent.isLoaded(),
                                         installed: Installer.isInstalled)
        for (index, section) in sections.enumerated() {
            if index > 0, !section.rows.isEmpty {
                menu.addItem(.separator())
            }
            for row in section.rows where row.kind != .separator {
                menu.addItem(menuItem(for: row))
            }
        }
    }

    private func menuItem(for row: MenuRow) -> NSMenuItem {
        let item = NSMenuItem(title: row.title,
                              action: row.action == nil ? nil : #selector(handleAction(_:)),
                              keyEquivalent: "")
        item.target = self
        item.isEnabled = row.enabled
        if let action = row.action { item.representedObject = action.rawValue }
        if let checked = row.checked {
            item.state = checked ? NSControl.StateValue.on : NSControl.StateValue.off
        }
        switch row.kind {
        case .header:
            item.attributedTitle = NSAttributedString(string: row.title, attributes: [
                .font: NSFont.systemFont(ofSize: 12, weight: .semibold),
                .foregroundColor: NSColor.labelColor,
            ])
        case .info:
            item.attributedTitle = NSAttributedString(string: row.title, attributes: [
                .font: NSFont.systemFont(ofSize: 12, weight: .regular),
                .foregroundColor: NSColor.secondaryLabelColor,
            ])
        default:
            break
        }
        return item
    }

    @objc private func handleAction(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String, let action = MenuAction(rawValue: raw) else { return }
        switch action {
        case .togglePause:
            core.setPaused(!snapshot.paused)
            log.info(snapshot.paused ? "菜单：继续监视" : "菜单：暂停监视")
        case .retryNow:
            core.retryNow()
        case .openNAS:
            if let url = settings.baseURL { NSWorkspace.shared.open(url) }
        case .openSettings:
            showSettings()
        case .toggleLoginItem:
            toggleLoginItem()
        case .selfCheck:
            showSelfCheck()
        case .install:
            showInstallPrompt()
        case .about:
            showAbout()
        case .uninstall:
            confirmUninstall()
        case .quit:
            core.stop()
            NSApp.terminate(nil)
        }
    }

    // MARK: - 登录项

    private func toggleLoginItem() {
        guard Installer.isInstalled else {
            showInstallPrompt()
            return
        }
        if LaunchAgent.isInstalled() {
            LaunchAgent.uninstall()
            log.info("登录项已移除")
        } else {
            let ok = LaunchAgent.install()
            log.info("登录项写入\(ok ? "成功" : "失败")")
            if !ok { showSimpleAlert(title: "没能写入登录项", message: "请稍后再试；也可以用「卸载」清干净后重新安装。") }
        }
    }

    // MARK: - 窗口

    private func showSettings() {
        if settingsWindow == nil {
            let model = SettingsModel(settings: settings,
                                      launchAtLogin: LaunchAgent.isInstalled() && LaunchAgent.isLoaded(),
                                      installed: Installer.isInstalled)
            model.onSaved = { [weak self] in
                self?.core.reloadSettings()
                self?.log.info("设置已保存 · 地址 \(self?.settings.baseURLString ?? "") · key \(self?.settings.keySourceDescription ?? "")")
            }
            model.onToggleLogin = { [weak self] want in
                guard let self else { return false }
                if want {
                    guard Installer.isInstalled else { self.showInstallPrompt(); return false }
                    return LaunchAgent.install()
                } else {
                    LaunchAgent.uninstall()
                    return false
                }
            }
            let hosting = NSHostingController(rootView: SettingsView(model: model))
            let window = NSWindow(contentViewController: hosting)
            window.title = "QBNAS 剪贴板代理 · 设置"
            window.styleMask = [.titled, .closable]
            window.isReleasedWhenClosed = false
            window.center()
            settingsWindow = window
        }
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
        (settingsWindow?.contentViewController as? NSHostingController<SettingsView>)?.rootView.model.reloadFromDisk()
    }

    private func showSelfCheck() {
        let model = SelfCheckModel(snapshot: snapshot, settings: settings, log: log)
        if selfCheckWindow == nil {
            let hosting = NSHostingController(rootView: SelfCheckView(model: model))
            let window = NSWindow(contentViewController: hosting)
            window.title = "自检与日志"
            window.styleMask = [.titled, .closable, .resizable]
            window.isReleasedWhenClosed = false
            window.setContentSize(NSSize(width: 560, height: 460))
            window.center()
            selfCheckWindow = window
        }
        NSApp.activate(ignoringOtherApps: true)
        selfCheckWindow?.makeKeyAndOrderFront(nil)
    }

    private func showInstallPrompt() {
        let model = InstallViewModel(installed: Installer.isInstalled,
                                     targetPath: Installer.installTargetDescription,
                                     onInstall: { [weak self] in
            guard let self else { return }
            do {
                try Installer.installAppBundle()
                self.log.info("已安装到 \(Installer.paths.app.path)")
                self.installWindow?.close()
                self.installWindow = nil
                NSWorkspace.shared.open(Installer.paths.app)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                    NSApp.terminate(nil)
                }
            } catch {
                self.showSimpleAlert(title: "安装没成功", message: "\(error)")
            }
        }, onLater: { [weak self] in
            self?.installWindow?.close()
            self?.installWindow = nil
        })
        if installWindow == nil {
            let hosting = NSHostingController(rootView: InstallView(model: model))
            let window = NSWindow(contentViewController: hosting)
            window.title = "安装 QBNAS 剪贴板代理"
            window.styleMask = [.titled, .closable]
            window.isReleasedWhenClosed = false
            window.center()
            installWindow = window
            window.delegate = nil
        }
        NSApp.activate(ignoringOtherApps: true)
        installWindow?.makeKeyAndOrderFront(nil)
    }

    private func showAbout() {
        let info = """
        QBNAS 剪贴板代理 \(AgentVersion.string)

        只读剪贴板文本，只认 magnet: 和 http(s)://，
        交给 NAS 入库；不在本地保存剪贴板全文。

        地址：\(settings.baseURLString)
        API Key：\(settings.keySourceDescription)
        登录项：\(LaunchAgent.isInstalled() ? "已开启" : "未开启")
        安装位置：\(Installer.currentBundle.path)
        """
        showSimpleAlert(title: "关于", message: info)
    }

    private func confirmUninstall() {
        let alert = NSAlert()
        alert.messageText = "卸载 QBNAS 剪贴板代理？"
        alert.informativeText = "会移除这个 app、登录项、本地队列和钥匙串里的 API Key。NAS 上的资源库和下载不受影响。"
        alert.addButton(withTitle: "卸载")
        alert.addButton(withTitle: "取消")
        alert.alertStyle = .warning
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            log.info("开始卸载")
            core.stop()
            Installer.uninstall()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { NSApp.terminate(nil) }
        }
    }

    private func showSimpleAlert(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: "好")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
}
