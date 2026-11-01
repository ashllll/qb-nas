import AppKit

// 生成 AppIcon.icns：深蓝底 + 白剪贴板 + 绿色状态点。
let sizes = [16, 32, 64, 128, 256, 512, 1024]
let outDir = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "build/AppIcon.iconset")
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

func render(_ size: Int) -> Data? {
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return nil }
    let ctx = NSGraphicsContext(bitmapImageRep: rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx
    let s = CGFloat(size)
    // 背景圆角方块（macOS 图标安全区）
    let inset = s * 0.06
    let bg = NSRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
    let path = NSBezierPath(roundedRect: bg, xRadius: s * 0.21, yRadius: s * 0.21)
    let gradient = NSGradient(colors: [NSColor(calibratedRed: 0.13, green: 0.30, blue: 0.47, alpha: 1),
                                       NSColor(calibratedRed: 0.09, green: 0.55, blue: 0.60, alpha: 1)])
    gradient?.draw(in: path, angle: -60)
    // 剪贴板
    let board = NSRect(x: s * 0.27, y: s * 0.19, width: s * 0.46, height: s * 0.55)
    let boardPath = NSBezierPath(roundedRect: board, xRadius: s * 0.055, yRadius: s * 0.055)
    NSColor.white.setFill()
    boardPath.fill()
    // 顶部夹子
    let clipRect = NSRect(x: s * 0.40, y: s * 0.69, width: s * 0.20, height: s * 0.09)
    NSColor(calibratedWhite: 0.95, alpha: 1).setFill()
    NSBezierPath(roundedRect: clipRect, xRadius: s * 0.035, yRadius: s * 0.035).fill()
    // 文本线
    NSColor(calibratedRed: 0.13, green: 0.30, blue: 0.47, alpha: 0.85).setFill()
    for (idx, width) in [0.30, 0.22, 0.26].enumerated() {
        let y = s * (0.48 - Double(idx) * 0.10)
        NSBezierPath(roundedRect: NSRect(x: s * 0.35, y: y, width: s * width, height: s * 0.045),
                     xRadius: s * 0.022, yRadius: s * 0.022).fill()
    }
    // 状态点
    NSColor(calibratedRed: 0.36, green: 0.86, blue: 0.53, alpha: 1).setFill()
    let dot = NSRect(x: s * 0.62, y: s * 0.10, width: s * 0.26, height: s * 0.26)
    NSBezierPath(ovalIn: dot).fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])
}

for size in sizes {
    for scale in [1, 2] {
        let px = size * scale
        guard px <= 1024, let data = render(px) else { continue }
        let name = scale == 1 ? "icon_\(size)x\(size).png" : "icon_\(size)x\(size)@2x.png"
        try? data.write(to: outDir.appendingPathComponent(name))
    }
}
print("iconset written to \(outDir.path)")
