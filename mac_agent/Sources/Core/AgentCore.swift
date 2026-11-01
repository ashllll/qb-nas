import Foundation

protocol PasteboardSource: AnyObject {
    var changeCount: Int { get }
    /// 只读字符串类型；读不到就返回 nil。
    func string() -> String?
}

struct AgentSnapshot: Equatable {
    var paused = false
    var online = false
    var keyConfigured = false
    var keyRejected = false
    var queueCount = 0
    var results: [ResultRecord] = []
    var baseURL = ""
    var lastHealthAt: Double?
    var lastSentAt: Double?
    var lastError: String?
    var lastCaptureAt: Double?
    var lastCaptureLabel: String?
    var version = AgentVersion.string

    static func == (lhs: AgentSnapshot, rhs: AgentSnapshot) -> Bool {
        lhs.paused == rhs.paused && lhs.online == rhs.online
            && lhs.keyConfigured == rhs.keyConfigured && lhs.keyRejected == rhs.keyRejected
            && lhs.queueCount == rhs.queueCount && lhs.results == rhs.results
            && lhs.baseURL == rhs.baseURL && lhs.lastError == rhs.lastError
            && lhs.lastSentAt == rhs.lastSentAt && lhs.lastCaptureLabel == rhs.lastCaptureLabel
            && lhs.lastHealthAt == rhs.lastHealthAt
    }
}

/// 全部状态都在 coreQueue 上改，UI 只拿不可变快照。
final class AgentCore: @unchecked Sendable {
    let coreQueue = DispatchQueue(label: "com.qbnas.macagent.core")
    private let store: QueueStore
    private let client: APIClient
    private let settings: Settings
    private let log: AgentLog
    private var pasteboard: PasteboardSource?

    private var timer: DispatchSourceTimer?
    private var lastChangeCount = -1
    private var state: AgentSnapshot
    private var nextHealthAt: Double = 0
    private var nextSendAt: Double = 0
    private var inFlightSend = false
    private var inFlightHealth = false
    private var cachedKey: String?
    private var keyCacheValidUntil: Double = 0
    private var didSeedClipboard = false

    /// 记录“离线从什么时候开始”，恢复后菜单可以说明补齐了多少条。
    private var offlineSince: Double?

    var onSnapshot: ((AgentSnapshot) -> Void)?
    var onCapture: ((ClipItem) -> Void)?

    init(settings: Settings, store: QueueStore, client: APIClient, log: AgentLog) {
        self.settings = settings
        self.store = store
        self.client = client
        self.log = log
        self.state = AgentSnapshot()
        self.state.paused = settings.paused
        self.state.baseURL = settings.baseURLString
        self.state.queueCount = store.queueCount
        self.state.results = store.results.suffix(6).reversed()
    }

    func attach(pasteboard: PasteboardSource) {
        coreQueue.sync { self.pasteboard = pasteboard }
    }

    func start() {
        coreQueue.sync {
            // 启动时把当前剪贴板当作“已处理”，这样重启/登录时不会把旧内容再收一遍。
            if let pasteboard {
                lastChangeCount = pasteboard.changeCount
            }
            didSeedClipboard = true
            state.queueCount = store.queueCount
            log.info("agent 启动 · 队列 \(store.queueCount) 条 · 地址 \(settings.baseURLString) · key \(settings.keySourceDescription)")
            if !store.entries.isEmpty {
                store.kickAll(now: now())
            }
            nextHealthAt = 0
        }
        let timer = DispatchSource.makeTimerSource(queue: coreQueue)
        timer.schedule(deadline: .now() + 0.2, repeating: 1.0, leeway: .milliseconds(100))
        timer.setEventHandler { [weak self] in self?.tick() }
        timer.resume()
        self.timer = timer
        publish(force: true)
    }

    func stop() {
        timer?.cancel()
        timer = nil
        coreQueue.sync { store.save() }
    }

    // MARK: - 用户动作

    func setPaused(_ paused: Bool) {
        coreQueue.sync {
            state.paused = paused
            settings.paused = paused
            // 暂停期间不读剪贴板，但记住位置，恢复后不会把暂停时复制的旧内容补收上来。
            if let pasteboard { lastChangeCount = pasteboard.changeCount }
            log.info(paused ? "已暂停监视" : "恢复监视")
        }
        publish(force: true)
    }

    func retryNow() {
        coreQueue.sync {
            store.kickAll(now: now())
            nextHealthAt = 0
            nextSendAt = 0
            log.info("用户要求立即重试")
        }
        tick()
    }

    func reloadSettings() {
        coreQueue.sync {
            state.baseURL = settings.baseURLString
            keyCacheValidUntil = 0
            nextHealthAt = 0
            nextSendAt = 0
        }
        tick()
    }

    func snapshot() -> AgentSnapshot {
        coreQueue.sync { state }
    }

    // MARK: - 主循环

    func tick() {
        coreQueue.async { [weak self] in
            guard let self else { return }
            let t = self.now()
            self.refreshKey(t)
            if !self.state.paused {
                self.pollPasteboard(t)
            } else if let pasteboard = self.pasteboard {
                self.lastChangeCount = pasteboard.changeCount
            }
            self.maybeProbeHealth(t)
            self.maybeSend(t)
            self.publish()
        }
    }

    private func now() -> Double { Date().timeIntervalSince1970 }

    private func refreshKey(_ t: Double) {
        if t < keyCacheValidUntil { return }
        cachedKey = settings.apiKey
        keyCacheValidUntil = t + 20
        state.keyConfigured = (cachedKey?.isEmpty == false)
    }

    private func pollPasteboard(_ t: Double) {
        guard let pasteboard else { return }
        let count = pasteboard.changeCount
        guard count != lastChangeCount else { return }
        lastChangeCount = count
        guard count > 0 else { return }
        guard let text = pasteboard.string(), !text.isEmpty else { return }
        guard let item = Classifier.classify(text) else {
            // 不匹配的内容连日志都不写内容，只记一次“忽略”。
            log.info("忽略一段剪贴板文本（不是磁力也不是单个网址）")
            return
        }
        guard store.shouldAccept(item, now: t) else {
            log.info("本地去重：\(item.kind.rawValue) \(item.shortLabel)")
            state.lastCaptureAt = t
            state.lastCaptureLabel = item.shortLabel
            publish(force: true)
            return
        }
        store.enqueue(item, now: t, nextAttemptAt: t)
        state.queueCount = store.queueCount
        state.lastCaptureAt = t
        state.lastCaptureLabel = item.shortLabel
        state.lastError = nil
        log.info("收下 \(item.kind.rawValue) \(item.shortLabel) · 队列 \(state.queueCount)")
        onCapture?(item)
        nextSendAt = t
        publish(force: true)
    }

    private func maybeProbeHealth(_ t: Double) {
        guard t >= nextHealthAt, !inFlightHealth else { return }
        inFlightHealth = true
        nextHealthAt = t + (state.online ? 10 : 4)
        let started = t
        Task { [weak self] in
            guard let self else { return }
            var success = false
            var failure: String?
            var unauthorized = false
            do {
                let info = try await self.client.health()
                success = info.ok
                if !info.ok { failure = "NAS 说不健康" }
            } catch let error as APIError {
                failure = error.shortDescription
                if case .unauthorized = error { unauthorized = true }
            } catch {
                failure = error.localizedDescription
            }
            self.coreQueue.async {
                self.inFlightHealth = false
                let now = self.now()
                let wasOnline = self.state.online
                self.state.online = success
                self.state.lastHealthAt = now
                self.state.keyRejected = unauthorized
                if success {
                    if !wasOnline {
                        self.log.info("NAS 恢复在线")
                        self.offlineSince = nil
                        self.store.kickAll(now: now)
                        self.nextSendAt = 0
                    }
                    self.state.lastError = self.state.queueCount > 0 ? self.state.lastError : nil
                    self.nextHealthAt = now + 10
                } else {
                    if wasOnline { self.log.warn("NAS 不在线：\(failure ?? "未知")") }
                    self.offlineSince = self.offlineSince ?? started
                    self.state.lastError = failure ?? "NAS 不在线"
                    self.nextHealthAt = now + 4
                }
                self.publish(force: true)
            }
        }
    }

    private func maybeSend(_ t: Double) {
        guard !inFlightSend, state.online, state.keyConfigured, t >= nextSendAt else { return }
        guard let entry = store.nextReady(now: t) else { return }
        inFlightSend = true
        nextSendAt = t + 0.3
        Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await self.client.ingest(entry)
                self.coreQueue.async {
                    self.inFlightSend = false
                    let now = self.now()
                    self.state.keyRejected = false
                    let result = ResultRecord(at: now,
                                              kind: entry.kind.rawValue,
                                              status: response.status,
                                              detail: self.detail(for: entry, response: response))
                    self.store.markSent(entry.id, result: result)
                    self.state.queueCount = self.store.queueCount
                    self.state.results = self.store.results.suffix(6).reversed()
                    self.state.lastSentAt = now
                    self.state.lastError = nil
                    self.log.info("上报成功 \(entry.kind.rawValue) \(entry.shortLabel) → \(response.status)\(response.reason.map { " · \($0)" } ?? "")")
                    self.publish(force: true)
                }
            } catch let error as APIError {
                self.coreQueue.async {
                    self.inFlightSend = false
                    self.handle(error: error, entry: entry)
                }
            } catch {
                self.coreQueue.async {
                    self.inFlightSend = false
                    self.handle(error: .offline(error.localizedDescription), entry: entry)
                }
            }
        }
    }

    private func handle(error: APIError, entry: QueueEntry) {
        let now = self.now()
        switch error {
        case .unauthorized:
            state.keyRejected = true
            state.lastError = "API Key 被拒绝，去「设置」里核对"
            store.markFailed(entry.id, error: "API Key 被拒绝", now: now, retryIn: 60)
            log.warn("API Key 被拒绝，暂停 60s 后重试")
        case .offline(let reason):
            state.online = false
            offlineSince = offlineSince ?? now
            state.lastError = error.shortDescription
            store.markFailed(entry.id, error: reason, now: now, retryIn: backoff(entry.attempts))
            nextHealthAt = now + 3
            log.warn("上报失败：\(error.shortDescription) · 队列 \(store.queueCount) 条，稍后重试")
        case .http(let code, _) where code == 400 || code == 404 || code == 422:
            // 服务明确不收这一条（例如 URL 安全校验没过），重试也没用，记为结果并出队。
            let result = ResultRecord(at: now, kind: entry.kind.rawValue, status: "rejected",
                                      detail: "NAS 拒收：\(error.shortDescription)")
            store.markSent(entry.id, result: result)
            state.queueCount = store.queueCount
            state.results = store.results.suffix(6).reversed()
            state.lastError = error.shortDescription
            log.warn("NAS 拒收 \(entry.kind.rawValue) \(entry.shortLabel)：\(error.shortDescription)")
        default:
            state.lastError = error.shortDescription
            store.markFailed(entry.id, error: error.shortDescription, now: now, retryIn: backoff(entry.attempts))
            log.warn("上报失败：\(error.shortDescription)")
        }
        publish(force: true)
    }

    private func backoff(_ attempts: Int) -> TimeInterval {
        let table: [TimeInterval] = [3, 6, 12, 30, 60, 120, 300]
        let base = table[min(attempts, table.count - 1)]
        return base + Double.random(in: 0...1.5)
    }

    private func detail(for entry: QueueEntry, response: IngestResponse) -> String {
        let label = entry.shortLabel
        switch response.status {
        case "accepted":
            return entry.kind == .magnet ? "已入库 · \(label)" : "已交给 NAS 检查 · \(label)"
        case "duplicate":
            return "已在库里 · \(label)"
        case "rejected":
            return "被拒收 · \(response.reason ?? "服务器未说原因")"
        default:
            if let reason = response.reason { return "\(response.status) · \(reason)" }
            return "\(response.status) · \(label)"
        }
    }

    private var lastPublished: AgentSnapshot?

    private func publish(force: Bool = false) {
        let snapshot = state
        if !force, let lastPublished, lastPublished == snapshot { return }
        lastPublished = snapshot
        DispatchQueue.main.async { [weak self] in
            self?.onSnapshot?(snapshot)
        }
        StatusReport.write(snapshot: snapshot, settings: settings)
    }
}

extension QueueEntry {
    var shortLabel: String {
        switch kind {
        case .magnet:
            if let range = value.range(of: "xt=urn:btih:", options: .caseInsensitive) {
                let tail = value[range.upperBound...]
                let end = tail.firstIndex(where: { $0 == "&" || $0 == "#" }) ?? tail.endIndex
                let raw = String(tail[tail.startIndex..<end])
                if let normalized = Classifier.normalizeInfohash(raw) { return Redact.hashPrefix(normalized) }
            }
            return "磁力"
        case .url:
            return Redact.host(value)
        }
    }
}
