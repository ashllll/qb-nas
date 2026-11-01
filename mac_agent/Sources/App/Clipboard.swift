import AppKit

/// 读剪贴板：只读纯文本，不改写、不清空、不留历史。每秒只看 changeCount 有没有变。
final class SystemPasteboard: PasteboardSource {
    private let pasteboard = NSPasteboard.general
    var changeCount: Int { pasteboard.changeCount }
    func string() -> String? { pasteboard.string(forType: .string) }
}
