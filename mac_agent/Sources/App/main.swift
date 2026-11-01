import AppKit

let arguments = CommandLine.arguments

if arguments.contains("--headless") {
    HeadlessRunner.run(arguments: arguments)
} else if arguments.contains("--install-check") {
    InstallCheck.run()
} else {
    let application = NSApplication.shared
    let delegate = AppDelegate()
    application.delegate = delegate
    application.setActivationPolicy(.accessory)
    application.run()
}
