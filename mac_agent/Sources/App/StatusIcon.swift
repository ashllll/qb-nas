import AppKit

enum StatusIcon {
    enum Visual { case online, offline, paused, keyMissing, keyRejected }

    static func image(for visual: Visual) -> NSImage {
        let size = NSSize(width: 22, height: 22)
        let image = NSImage(size: size, flipped: false) { rect in
            let glyph = NSBezierPath()
            // 剪贴板外形
            let body = NSRect(x: 4.5, y: 3.5, width: 13, height: 15)
            glyph.appendRoundedRect(body, xRadius: 2.6, yRadius: 2.6)
            let clip = NSRect(x: 8.2, y: 17.2, width: 5.6, height: 2.6)
            glyph.appendRoundedRect(clip, xRadius: 1.2, yRadius: 1.2)
            NSColor.labelColor.setStroke()
            glyph.lineWidth = 1.5
            glyph.stroke()
            // 中间两条短线，像“正在看一行文本”
            let lines = NSBezierPath()
            lines.move(to: NSPoint(x: 7.6, y: 13.2)); lines.line(to: NSPoint(x: 14.4, y: 13.2))
            lines.move(to: NSPoint(x: 7.6, y: 10.2)); lines.line(to: NSPoint(x: 12.2, y: 10.2))
            lines.lineWidth = 1.3
            lines.lineCapStyle = .round
            NSColor.labelColor.withAlphaComponent(0.75).setStroke()
            lines.stroke()

            // 右下角状态点
            let color: NSColor
            switch visual {
            case .online: color = .systemGreen
            case .offline: color = .systemRed
            case .paused: color = .systemGray
            case .keyMissing: color = .systemOrange
            case .keyRejected: color = .systemRed
            }
            let dotRect = NSRect(x: 13.4, y: 1.6, width: 7, height: 7)
            NSColor.windowBackgroundColor.setFill()
            NSBezierPath(ovalIn: dotRect.insetBy(dx: -1.1, dy: -1.1)).fill()
            color.setFill()
            NSBezierPath(ovalIn: dotRect).fill()
            _ = rect
            return true
        }
        image.isTemplate = false
        image.size = size
        return image
    }
}
