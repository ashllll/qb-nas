import Foundation

enum MenuAction: String {
    case togglePause, retryNow, openNAS, openSettings, toggleLoginItem, selfCheck, install, about, uninstall, quit
}

struct MenuRow: Equatable {
    enum Kind: Equatable { case header, info, item, separator }
    var kind: Kind
    var title: String
    var enabled: Bool = true
    var checked: Bool? = nil
    var action: MenuAction? = nil

    static func header(_ title: String) -> MenuRow { MenuRow(kind: .header, title: title, enabled: false) }
    static func info(_ title: String) -> MenuRow { MenuRow(kind: .info, title: title, enabled: false) }
    static func item(_ title: String, _ action: MenuAction, enabled: Bool = true) -> MenuRow {
        MenuRow(kind: .item, title: title, enabled: enabled, action: action)
    }
    static func toggle(_ title: String, _ action: MenuAction, on: Bool) -> MenuRow {
        MenuRow(kind: .item, title: title, enabled: true, checked: on, action: action)
    }
    static var separator: MenuRow { MenuRow(kind: .separator, title: "") }
}

struct MenuSection: Equatable {
    var rows: [MenuRow]
}

/// 菜单文案是这一块对用户最直接的部分，单独做成纯函数，便于测试各种状态。
enum MenuBuilder {
    static func build(snapshot: AgentSnapshot,
                      launchAtLogin: Bool,
                      installed: Bool,
                      maxResults: Int = 4) -> [MenuSection] {
        var sections: [MenuSection] = []
        sections.append(MenuSection(rows: statusRows(snapshot: snapshot)))
        sections.append(MenuSection(rows: resultRows(snapshot: snapshot, maxResults: maxResults)))
        sections.append(MenuSection(rows: actionRows(snapshot: snapshot)))
        sections.append(MenuSection(rows: launchRows(launchAtLogin: launchAtLogin, installed: installed)))
        sections.append(MenuSection(rows: footerRows()))
        return sections
    }

    static func statusText(_ s: AgentSnapshot) -> String {
        if s.keyRejected { return "API Key 被拒绝" }
        if !s.keyConfigured { return "还没设置 API Key" }
        if s.paused { return "已暂停监视" }
        return s.online ? "NAS 在线" : "NAS 不在线"
    }

    private static func statusRows(snapshot s: AgentSnapshot) -> [MenuRow] {
        var rows: [MenuRow] = [.header(statusText(s))]
        let address = prettyAddress(s.baseURL)
        if s.paused {
            rows.append(.info("\(address) · 待发 \(s.queueCount) 条"))
        } else if s.online {
            rows.append(.info("\(address) · 队列 \(s.queueCount) 条"))
        } else {
            rows.append(.info("\(address) · 待发 \(s.queueCount) 条"))
            rows.append(.info("连上以后自动补发，先复制不影响"))
        }
        if s.keyRejected || !s.keyConfigured {
            rows.append(.item("设置 API Key…", .openSettings))
        }
        return rows
    }

    private static func resultRows(snapshot s: AgentSnapshot, maxResults: Int) -> [MenuRow] {
        var rows: [MenuRow] = [.separator, .header("最近结果")]
        if s.results.isEmpty {
            rows.append(.info("还没有记录"))
            return rows
        }
        for record in s.results.prefix(maxResults) {
            rows.append(.info(resultLine(record)))
        }
        return rows
    }

    static func resultLine(_ record: ResultRecord) -> String {
        let time = timeText(record.at)
        let kind = record.kind == ClipKind.magnet.rawValue ? "磁力" : "网址"
        return "\(time) · \(kind) · \(truncate(record.detail, 30))"
    }

    private static func actionRows(snapshot s: AgentSnapshot) -> [MenuRow] {
        [.separator,
         .toggle(s.paused ? "继续监视" : "暂停监视", .togglePause, on: s.paused),
         .item("现在重试", .retryNow, enabled: !s.online || s.queueCount > 0 || s.keyRejected),
         .item("打开 NAS 页面", .openNAS, enabled: URL(string: normalized(s.baseURL)) != nil),
         .item("设置…", .openSettings)]
    }

    private static func launchRows(launchAtLogin: Bool, installed: Bool) -> [MenuRow] {
        var rows: [MenuRow] = [.separator]
        if installed {
            rows.append(.toggle("登录时启动", .toggleLoginItem, on: launchAtLogin))
        } else {
            rows.append(.item("安装到「应用程序」…", .install))
        }
        rows.append(.item("自检与日志…", .selfCheck))
        return rows
    }

    private static func footerRows() -> [MenuRow] {
        [.separator,
         .item("卸载…", .uninstall),
         .item("关于 QBNAS 剪贴板代理", .about),
         .item("退出", .quit)]
    }

    // MARK: - 小工具

    static func prettyAddress(_ raw: String) -> String {
        guard let url = URL(string: normalized(raw)) else { return raw }
        let host = url.host ?? raw
        let port = url.port.map { ":\($0)" } ?? ""
        return host + port
    }

    private static func normalized(_ raw: String) -> String {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.lowercased().hasPrefix("http") { return text }
        return "http://" + text
    }

    static func timeText(_ epoch: Double) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: Date(timeIntervalSince1970: epoch))
    }

    static func truncate(_ text: String, _ limit: Int) -> String {
        if text.count <= limit { return text }
        return String(text.prefix(limit - 1)) + "…"
    }
}
