import Foundation

setvbuf(stdout, nil, _IONBF, 0)

// 纯逻辑自测：分类、归一化、去重、队列落盘、菜单文案、登录项 plist、卸载脚本。
// 不碰网络、不碰剪贴板、不碰钥匙串，可以随时跑。

var failures = 0
var checks = 0

func check(_ condition: Bool, _ name: String, detail: @autoclosure () -> String = "") {
    checks += 1
    if condition {
        print("  ok   \(name)")
    } else {
        failures += 1
        print("  FAIL \(name) \(detail())")
    }
}

func tempDir(_ tag: String) -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("qbnas-selftest-\(tag)-\(UUID().uuidString)")
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

let hex = "abcdef0123456789abcdef0123456789abcdef01"

print("== 分类：磁力")
let magnet = "magnet:?xt=urn:btih:\(hex.uppercased())&dn=Movie.2160p"
if let item = Classifier.classify(magnet) {
    check(item.kind == .magnet, "认得磁力")
    check(item.infohash == hex, "infohash 归一化为小写 40 位", detail: item.infohash ?? "nil")
    check(item.value == magnet, "value 原样保留")
} else { check(false, "认得磁力") }

let base32 = "MFRGGZDFMZTWQ2LKNNWG23TPOBYXE43U"
if let decoded = Classifier.normalizeInfohash(base32) {
    check(decoded.count == 40 && decoded.allSatisfy { $0.isHexDigit }, "base32 → 40 位十六进制", detail: decoded)
} else { check(false, "base32 解码") }

check(Classifier.classify("这是一段文字\n\(magnet)\n还有别的")?.infohash == hex, "文字里夹着的磁力能取出来")
check(Classifier.classify(magnet + "。")?.value.hasSuffix("。") == false, "末尾中文标点会被去掉")
check(Classifier.classify("magnet:?dn=没有btih") == nil, "没有 btih 的 magnet 不算")
check(Classifier.classify("magnet:?xt=urn:btih:1234") == nil, "infohash 太短不算")

print("== 分类：网址")
let url = "https://example.com/torrents/details/12345"
if let item = Classifier.classify(url) {
    check(item.kind == .url, "认得网址")
    check(item.host == "example.com", "取出域名", detail: item.host ?? "nil")
} else { check(false, "认得网址") }
check(Classifier.classify("  \(url)\n") != nil, "前后换行/空格能修剪")
check(Classifier.classify("看看这个 \(url) 挺好") == nil, "一句话里夹网址不收（避免收走整段文本）")
check(Classifier.classify("http://192.168.1.1:8080/a") != nil, "localhost/内网地址在 Mac 端也认（安全校验在 NAS）")
check(Classifier.classify("ftp://example.com/x") == nil, "只认 http(s)")
check(Classifier.classify("file:///etc/passwd") == nil, "file:// 不认")
check(Classifier.classify("https://example.com/" + String(repeating: "a", count: 3000)) == nil, "超长网址不收")

print("== 分类：其他文本")
check(Classifier.classify("我的密码是 hunter2") == nil, "普通文本忽略")
check(Classifier.classify("") == nil, "空文本忽略")
check(Classifier.classify(String(repeating: "x", count: 20_000)) == nil, "超大文本直接不看")
check(Classifier.clipDigestsDiffer(), "不同内容的摘要不同")

print("== 去重与队列")
let dir = tempDir("queue")
let store = QueueStore(dir: dir)
let now = 1_000_000.0
let item1 = Classifier.classify(magnet)!
check(store.shouldAccept(item1, now: now), "第一次收下")
let entry1 = store.enqueue(item1, now: now, nextAttemptAt: now)
check(!store.shouldAccept(item1, now: now + 10), "还在队列里时重复复制不再收")
let otherMagnet = magnet.replacingOccurrences(of: hex.uppercased(), with: String(repeating: "1", count: 40))
let item2 = Classifier.classify(otherMagnet)!
check(store.shouldAccept(item2, now: now), "另一条磁力可以收")
store.enqueue(item2, now: now, nextAttemptAt: now + 99)
check(store.queueCount == 2, "队列里 2 条")
check(store.nextReady(now: now + 1)?.value == magnet, "到点的先发（另一条还在退避）")

let reloaded = QueueStore(dir: dir)
check(reloaded.queueCount == 2, "重启后队列还在（持久化）", detail: "\(reloaded.queueCount)")
reloaded.markSent(entry1.id, result: ResultRecord(at: now, kind: "magnet", status: "accepted", detail: "已入库 · abcdef01…"))
check(reloaded.queueCount == 1, "发出去的就出队")
check(QueueStore(dir: dir).results.count == 1, "结果也落盘，重启能看到最近结果")
check(!QueueStore(dir: dir).shouldAccept(item1, now: now + 50), "去重记录也跨重启（窗口内）")
check(QueueStore(dir: dir).shouldAccept(item1, now: now + 301), "过了窗口期允许再收（比如重新检查同一个网址）")

let dir2 = tempDir("backoff")
let store2 = QueueStore(dir: dir2)
store2.enqueue(item1, now: now, nextAttemptAt: now)
store2.enqueue(item2, now: now, nextAttemptAt: now)
let firstId = store2.nextReady(now: now)!.id
let secondId = store2.entries.first { $0.id != firstId }!.id
store2.markFailed(secondId, error: "offline", now: now, retryIn: 30)
check(store2.nextReady(now: now + 1)?.id == firstId, "一条失败退避后，另一条还能发")
store2.markSent(firstId, result: ResultRecord(at: now, kind: "magnet", status: "accepted", detail: "ok"))
check(store2.nextReady(now: now + 5) == nil, "失败的条目在退避期内不重发")
check(store2.nextReady(now: now + 31)?.id == secondId, "退避到期后重发")
store2.kickAll(now: now + 32)
check(store2.nextReady(now: now + 32)?.id == secondId, "用户点「现在重试」后立刻可发")

print("== 菜单文案")
var snapshot = AgentSnapshot()
snapshot.keyConfigured = true
snapshot.online = true
check(MenuBuilder.statusText(snapshot) == "NAS 在线", "在线文案")
snapshot.online = false
check(MenuBuilder.statusText(snapshot) == "NAS 不在线", "离线文案")
snapshot.paused = true
check(MenuBuilder.statusText(snapshot) == "已暂停监视", "暂停文案")
snapshot.paused = false
snapshot.keyConfigured = false
check(MenuBuilder.statusText(snapshot) == "还没设置 API Key", "没配 key 文案")
snapshot.keyConfigured = true
snapshot.keyRejected = true
check(MenuBuilder.statusText(snapshot) == "API Key 被拒绝", "key 被拒文案")
snapshot.keyRejected = false

snapshot.baseURL = "http://192.168.20.193:8899"
snapshot.queueCount = 3
snapshot.online = false
let rows = MenuBuilder.build(snapshot: snapshot, launchAtLogin: true, installed: true).flatMap { $0.rows }
check(rows.contains { $0.title.contains("192.168.20.193:8899") && $0.title.contains("3") }, "菜单显示地址与待发条数")
check(rows.contains { $0.title == "现在重试" && $0.enabled }, "离线时「现在重试」可点")
snapshot.online = true
snapshot.queueCount = 0
let rowsOnline = MenuBuilder.build(snapshot: snapshot, launchAtLogin: true, installed: true).flatMap { $0.rows }
check(rowsOnline.contains { $0.title == "现在重试" && !$0.enabled }, "在线且队列空时「现在重试」置灰")
check(rowsOnline.contains { $0.title == "暂停监视" && $0.checked == false }, "暂停项未勾选")
check(rowsOnline.contains { $0.title == "登录时启动" && ($0.checked ?? false) }, "登录项已勾选")
let notInstalledRows = MenuBuilder.build(snapshot: snapshot, launchAtLogin: false, installed: false).flatMap { $0.rows }
check(notInstalledRows.contains { $0.title.contains("安装到") }, "未安装时菜单给安装入口")
let line = MenuBuilder.resultLine(ResultRecord(at: 1_700_000_000, kind: "magnet", status: "accepted", detail: "已入库 · abcdef01…"))
check(line.contains("磁力") && line.contains("已入库") && line.contains("·"), "最近结果一行写清时间/类型/结果", detail: line)

print("== 登录项与安装")
let paths = LaunchAgent.Paths(home: URL(fileURLWithPath: "/tmp/fake-home"))
check(paths.plist.lastPathComponent == "com.qbnas.macagent.plist", "登录项文件名固定")
check(paths.app.lastPathComponent == "NAS 剪贴板代理.app", "app 安装名固定")
let plist = LaunchAgent.plistObject(binaryPath: paths.binary.path)
check(plist["Label"] as? String == "com.qbnas.macagent", "Label 固定")
check((plist["ProgramArguments"] as? [String])?.first == paths.binary.path, "ProgramArguments 指向安装后的可执行文件")
check((plist["RunAtLoad"] as? Bool) == true, "RunAtLoad")
check((plist["KeepAlive"] as? [String: Bool])?["SuccessfulExit"] == false, "崩溃才重启（菜单退出不会拉起）")
let data = LaunchAgent.plistData(binaryPath: paths.binary.path)!
let parsed = try! PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any]
check(parsed?["Label"] as? String == "com.qbnas.macagent", "plist 能序列化成合法 XML")
let script = Installer.uninstallScript(paths: paths, uid: 501)
check(script.contains("bootout gui/501/com.qbnas.macagent"), "卸载脚本摘登录项")
check(script.contains(paths.plist.path) && script.contains(paths.app.path), "卸载脚本删 plist 与 app")
check(script.contains(paths.supportDir.path), "卸载脚本删本地数据")

print("== 摘要工具")
check(Redact.hashPrefix(hex).count == 9, "哈希只露前 8 位")
check(Redact.host("https://example.com/a/b?c=d") == "example.com", "日志里只写域名")

func item2DigestEntryId(_ store: QueueStore) -> String {
    store.nextReady(now: 1_000_000_000)?.id ?? "missing"
}

print("")
print(failures == 0 ? "通过 \(checks) 项" : "失败 \(failures)/\(checks) 项")
exit(failures == 0 ? 0 : 1)
