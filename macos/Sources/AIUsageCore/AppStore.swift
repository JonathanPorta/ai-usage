import Foundation
import Observation

/// The one shared application store. Owned by the App and injected into the
/// MenuBarExtra popover and the History window, so both always render the same
/// snapshot. Views read it; refreshes, Collect now, service actions and settings write to it.
///
/// Latency rules (the report scans a large CSV and takes seconds):
/// - the last good snapshot renders immediately, from memory or the on-disk cache;
/// - all refreshes funnel through one worker: overlapping requests coalesce, and
///   an older result can never replace a newer one;
/// - opening the popover or switching views never scans unless the collector's
///   files changed since the last scan;
/// - a failed refresh keeps the last good snapshot and surfaces the error;
/// - the snapshot is re-evaluated against the clock (`Report.evaluated(at:)`), so
///   cached readings can never look fresher than they are.
@MainActor
@Observable
public final class AppStore {
    public enum Phase: Equatable {
        case loading
        case ready
        case failed(String)
    }

    public enum CollectState: Equatable {
        case idle
        case running(startedAt: Date, progress: CollectProgress?)
        case finished(CollectRun)
    }

    public enum ActionState: Equatable {
        case idle
        case running(String)
        case failed(String)
        case done(String)
    }

    // MARK: Observable state

    /// The last good snapshot exactly as the reporting layer produced it.
    public private(set) var snapshot: Report?
    public private(set) var phase: Phase = .loading
    /// Error from the most recent refresh, even when an older snapshot is still shown.
    public private(set) var refreshError: String?
    public private(set) var isRefreshing = false
    public private(set) var lastRefreshAt: Date?
    public private(set) var collect: CollectState = .idle
    /// Increments whenever the snapshot changes; History and other observers key on it.
    public private(set) var revision = 0
    /// True until a live report replaces the snapshot loaded from the on-disk cache.
    public private(set) var isCachedReport = false
    /// Number of full report runs (each scans the CSV). Used by tests and measurement.
    public private(set) var reportRuns = 0
    /// The latest cheap service probe, with when it was taken.
    public private(set) var liveService: ServiceObservation?
    /// Lifecycle and settings actions (start/stop, pause/resume, config writes).
    public private(set) var action: ActionState = .idle
    /// True from an add-account submission's start until its refresh ends.
    public private(set) var isAddingAccount = false
    /// The file-defined theme in effect (docs/THEMES.md); built-in colors when no file.
    public private(set) var theme: ThemeState = .builtIn

    /// The snapshot as it reads now: calendar rollover and deadlines applied, and
    /// the service state taken from whichever observation is newer (probe or report).
    public var report: Report? {
        guard let snapshot else { return nil }
        var current = snapshot.evaluated(at: clock())
        if let liveService,
           snapshot.service.state == .unknown || liveService.observedAt > snapshot.generatedAt {
            current.service = liveService.status.asReportService
        }
        return current
    }

    public let environment: CollectorEnvironment
    private let reporter: ReportFetching
    private let collector: CollectorRunning
    private let service: ServiceControlling
    private let config: ConfigWriting
    private let clock: @Sendable () -> Date
    private let cacheURL: URL?
    private let fingerprint: @Sendable ([URL]) -> SourceFingerprint
    private var timer: Timer?
    @ObservationIgnored private let themeStore: ThemeStore?

    private var requested = 0
    private var completed = 0
    private var worker: Task<Void, Never>?
    private var scannedFingerprint: SourceFingerprint?
    /// Local date (report timezone) when the last successful scan started.
    private var scannedDay: String?

    public static let backgroundCheckInterval: TimeInterval = 5 * 60

    /// The daemon applies a pause change on its next tick (at most 15 s), and a started
    /// daemon schedules after its start-up check; only then is the new schedule logged.
    @ObservationIgnored public var scheduleSettleDelay: Duration = .seconds(16)
    @ObservationIgnored public private(set) var scheduleFollowUp: Task<Void, Never>?

    public init(
        environment: CollectorEnvironment,
        reporter: ReportFetching,
        collector: CollectorRunning,
        service: ServiceControlling = NoServiceControl(),
        config: ConfigWriting = NoConfigWriter(),
        clock: @escaping @Sendable () -> Date = Date.init,
        cacheURL: URL? = nil,
        fingerprint: @escaping @Sendable ([URL]) -> SourceFingerprint = SourceFingerprint.of,
        preferences: PreferencesStoring = InMemoryPreferences(),
        themeDirectory: URL? = nil
    ) {
        self.preferences = preferences
        let themeStore = themeDirectory.map { ThemeStore(directory: $0) }
        self.themeStore = themeStore
        theme = themeStore?.state ?? .builtIn
        cardOrderMode = preferences.string(forKey: PreferenceKey.cardOrderMode).flatMap(CardOrderMode.init) ?? .recent
        customCardOrder = preferences.strings(forKey: PreferenceKey.customCardOrder) ?? []
        acknowledgedDetections = Set(preferences.strings(forKey: PreferenceKey.acknowledgedDetections) ?? [])
        acknowledgedWarnings = preferences.string(forKey: PreferenceKey.acknowledgedWarnings)
        self.environment = environment
        self.reporter = reporter
        self.collector = collector
        self.service = service
        self.config = config
        self.clock = clock
        self.cacheURL = cacheURL
        self.fingerprint = fingerprint
    }

    public static func live() -> AppStore {
        let environment = CollectorEnvironment.resolve()
        return AppStore(
            environment: environment,
            reporter: LiveReportClient(environment: environment),
            collector: LiveCollectorClient(environment: environment),
            // Sandbox runs (AI_USAGE_SERVICE=skip) must never reach the real launchd domain.
            service: environment.skipServiceProbe ? NoServiceControl() : LiveServiceControl(environment: environment),
            config: LiveConfigWriter(environment: environment),
            cacheURL: environment.stateDirectory.appendingPathComponent("last-report.json"),
            preferences: UserDefaultsPreferences(AppDefaults.shared),
            // Beside the report cache: survives app replacement and collector reinstall.
            themeDirectory: environment.stateDirectory
        )
    }

    /// A store pre-filled with a report, for previews, snapshots and tests. Never used by the running app.
    public static func preview(_ report: Report, clock: Date? = nil) -> AppStore {
        let store = AppStore(
            environment: CollectorEnvironment(
                python: URL(fileURLWithPath: "/usr/bin/python3"), collectorScript: nil,
                configPath: URL(fileURLWithPath: "/sandbox/config.json"), reportScript: nil,
                skipServiceProbe: true, stateDirectory: URL(fileURLWithPath: NSTemporaryDirectory()),
                childEnvironment: [:]),
            reporter: StaticReport(report: report), collector: NoCollector(),
            clock: { clock ?? Date() })
        store.apply(report, cached: false)
        return store
    }

    // MARK: Themes

    /// `<state directory>/themes.json`, or nil for previews and snapshots (in-memory only).
    public var themeFileURL: URL? { themeStore?.url }

    /// Re-reads the theme file; an invalid file keeps the last valid theme.
    public func reloadThemes() {
        guard let themeStore else { return }
        theme = themeStore.reload()
    }

    /// Creates the starter theme file if there is none and returns its location.
    public func prepareThemeFile() throws -> URL? {
        guard let url = themeFileURL else { return nil }
        if try ThemeLoader.ensureFile(url) { reloadThemes() }
        return url
    }

    /// Snapshots and tests: show a theme without any file.
    public func previewTheme(_ state: ThemeState) { theme = state }

    public var isCollecting: Bool {
        if case .running = collect { return true }
        return false
    }

    public var isActing: Bool {
        if case .running = action { return true }
        return false
    }

    public var now: Date { clock() }

    // MARK: Lifecycle

    /// Launch: render the cached snapshot instantly, then refresh in the background.
    public func start() {
        loadCache()
        Task { await refresh() }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: Self.backgroundCheckInterval, repeats: true) { [weak self] _ in
            Task { @MainActor in
                // Service state is checked independently of any CSV change.
                await self?.probeService()
                await self?.refreshIfSourcesChanged()
            }
        }
    }

    /// Popover opened: never collects, and only re-reads when the collector's files
    /// changed since the last scan. The service probe is cheap and always runs.
    public func popoverOpened() {
        snapshotRecentOrder()
        Task {
            await probeService()
            await refreshIfSourcesChanged()
        }
    }

    /// Re-run the report only if the CSV, log or config changed since the last scan,
    /// or the calendar date (in the report's timezone) moved past the snapshot's day.
    public func refreshIfSourcesChanged() async {
        // One re-read per new calendar day, judged against when we last scanned,
        // so a report that lags the clock can't cause a rescan on every reopen.
        let dayChanged = snapshot.map { $0.localDay(clock()) != (scannedDay ?? $0.today) } ?? false
        if !dayChanged, let scannedFingerprint, scannedFingerprint == fingerprint(sourceURLs) { return }
        await refresh()
    }

    /// Request a report run. Coalesces with any run in flight: callers return once a
    /// run that started after their request has finished.
    public func refresh() async {
        requested += 1
        let wanted = requested
        while completed < wanted {
            if worker == nil { worker = Task { await self.drain() } }
            await worker?.value
        }
    }

    private func drain() async {
        while completed < requested {
            let target = requested
            isRefreshing = true
            let before = fingerprint(sourceURLs)
            let startedAt = clock()
            reportRuns += 1
            do {
                let fresh = try await reporter.fetch()
                // Never let an older result replace a newer snapshot.
                if let current = snapshot, fresh.generatedAt < current.generatedAt {
                    // Discard; the newer snapshot stays.
                } else {
                    apply(fresh, cached: false)
                    saveCache(fresh)
                }
                refreshError = nil
                // The first report names the CSV and log; fingerprint those too.
                let after = fingerprint(sourceURLs)
                scannedFingerprint = Set(after.entries.keys) == Set(before.entries.keys) ? before : after
                scannedDay = snapshot?.localDay(startedAt)
            } catch {
                let message = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
                refreshError = message
                if snapshot == nil { phase = .failed(message) }
            }
            lastRefreshAt = clock()
            completed = target
        }
        isRefreshing = false
        worker = nil
    }

    private var sourceURLs: [URL] {
        var urls = [environment.configPath]
        if let collector = snapshot?.collector {
            urls.append(URL(fileURLWithPath: collector.usageCsv))
            urls.append(URL(fileURLWithPath: collector.logFile))
        }
        return urls
    }

    // MARK: Collect now

    /// The collector's existing one-time path, one run at a time, followed by a
    /// report refresh that every observing view (popover, History) receives.
    @discardableResult
    public func collectNow() async -> CollectRun? {
        guard !isCollecting else { return nil }
        collect = .running(startedAt: clock(), progress: nil)
        let run: CollectRun
        do {
            run = try await collector.collectOnce(progressSupported: snapshot?.collector.capabilities?.progress ?? false) { [weak self] progress in
                Task { @MainActor in
                    guard let self, case let .running(started, _) = self.collect else { return }
                    self.collect = .running(startedAt: started, progress: progress)
                }
            }
        } catch {
            let message = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
            run = CollectRun(outcome: .failed, message: message, finishedAt: clock())
        }
        await refresh()
        // A check newer than this run may already be in the report the refresh read.
        collect = Self.superseded(run, by: snapshot) ? .idle : .finished(run)
        return run
    }

    public func dismissCollectResult() {
        if case .finished = collect { collect = .idle }
    }

    // MARK: Service and settings

    public func probeService() async {
        let started = clock()
        guard let status = try? await service.status() else { return }
        // A probe that started earlier than the current observation is older evidence.
        if let current = liveService, current.observedAt > started { return }
        liveService = ServiceObservation(status: status, observedAt: started)
    }

    public func startService() async {
        await perform("Starting the collector…", success: "Collector started") { try await self.service.start() }
        followUpAfterScheduleChange()
    }

    public func stopService() async { await perform("Stopping the collector…", success: "Collector stopped; it stays stopped at login") { try await self.service.stop() } }

    public func setPaused(_ paused: Bool) async {
        await perform(paused ? "Pausing scheduled checks…" : "Resuming scheduled checks…",
                      success: paused ? "Scheduled checks paused" : "Scheduled checks resumed") {
            try await self.config.apply([.pollPaused(paused)])
        }
        followUpAfterScheduleChange()
    }

    /// The daemon logs a new schedule only after its next tick or its start-up check,
    /// so re-read once after that (only if the collector's files changed).
    private func followUpAfterScheduleChange() {
        scheduleFollowUp?.cancel()
        let delay = scheduleSettleDelay
        scheduleFollowUp = Task { [weak self] in
            try? await Task.sleep(for: delay)
            guard !Task.isCancelled else { return }
            await self?.refreshIfSourcesChanged()
        }
    }

    /// Returns how the save ended; nil when it did not run (nothing to save, or another action was running).
    @discardableResult
    public func applySettings(_ changes: [ConfigChange]) async -> ActionState? {
        guard !changes.isEmpty else { return nil }
        // Another action is running: keep these and write them when it ends, so an edit
        // committed on blur, leaving Settings or closing the panel is never dropped.
        guard !isActing else { queuedSettings += changes; return nil }
        return await perform("Saving settings…", success: "Settings saved") { try await self.config.apply(changes) }
    }

    /// Settings changes made while another action ran; written once it ends.
    @ObservationIgnored private var queuedSettings: [ConfigChange] = []

    private func applyQueuedSettings() async {
        guard !queuedSettings.isEmpty, !isActing else { return }
        let changes = queuedSettings
        queuedSettings = []
        await applySettings(changes)
    }

    /// Adds an account through the collector.
    @discardableResult
    public func addAccount(_ request: AccountRequest) async -> AccountSaveResult {
        // Busy until its refresh ends, not just its write: the banner reads done before then.
        guard !isAddingAccount else { return .busy }
        isAddingAccount = true
        defer { isAddingAccount = false }
        let outcome = await perform("Adding \(request.label)…", success: "\(request.label) added. It is read at the next check.") {
            try await self.config.addAccount(request)
        }
        switch outcome {
        case .done: return .saved
        case let .failed(reason): return .failed(reason)
        default: return .busy
        }
    }

    /// Runs one action and returns how it ended (`nil` when another was still running),
    /// taken before the follow-up probe and refresh can let other actions change `action`.
    @discardableResult
    private func perform(_ running: String, success: String, _ body: @escaping () async throws -> Void) async -> ActionState? {
        guard !isActing else { return nil }
        action = .running(running)
        let outcome: ActionState
        do {
            try await body()
            outcome = .done(success)
        } catch {
            outcome = .failed((error as? LocalizedError)?.errorDescription ?? String(describing: error))
        }
        action = outcome
        await probeService()
        await refresh()
        await applyQueuedSettings()
        return outcome
    }

    /// Shows an action state without running anything. Previews and snapshot renders only.
    public func previewAction(_ state: ActionState) { action = state }

    public func dismissAction() {
        if case .running = action { return }
        action = .idle
    }

    // MARK: Internals

    /// Called on the main actor after every new snapshot (not for cache loads);
    /// the app uses it to plan notifications.
    public var onSnapshotChange: ((Report) -> Void)?

    /// A Collect now result describes one check. Once a later check (the scheduled one) is
    /// in the report, the result gives way, so it can never hide that check's problems.
    static func superseded(_ run: CollectRun, by report: Report?) -> Bool {
        guard let latest = report?.collection.lastAttemptAt else { return false }
        return latest > run.finishedAt
    }

    func apply(_ newReport: Report, cached: Bool) {
        snapshot = newReport
        if case let .finished(run) = collect, Self.superseded(run, by: newReport) { collect = .idle }
        isCachedReport = cached
        phase = .ready
        revision += 1
        if recentOrderSnapshot == nil { snapshotRecentOrder() }
        if !cached { pruneDetectionAcknowledgements(); clearStaleWarningAcknowledgement() }
        if !cached, let report { onSnapshotChange?(report) }
    }

    // MARK: Card order, visibility and detection notices

    @ObservationIgnored private let preferences: PreferencesStoring
    public private(set) var cardOrderMode: CardOrderMode
    /// Saved instance-id order for `.custom`; includes hidden instances so they keep their slot.
    public private(set) var customCardOrder: [String]
    /// The automatic order taken when the popover opened; data refreshes never change it.
    public private(set) var recentOrderSnapshot: [String]?
    public private(set) var acknowledgedDetections: Set<String>
    /// The warning signature the user last looked at; see `warningsAcknowledged`.
    public private(set) var acknowledgedWarnings: String?

    /// Every report provider in display order (hidden ones included).
    public var orderedProviders: [Provider] {
        guard let providers = report?.providers else { return [] }
        switch cardOrderMode {
        case .custom: return CardOrdering.arrange(providers, by: customCardOrder)
        case .recent: return CardOrdering.arrange(providers, by: recentOrderSnapshot ?? CardOrdering.recent(providers))
        }
    }

    /// The one list both the overview cards and keyboard navigation use: visible, in display order.
    public var cardProviders: [Provider] { orderedProviders.filter(\.isOverviewVisible) }

    /// A report is loaded but no provider is visible: show "Choose your providers".
    /// Excludes first run: while any provider is pending, see `isAwaitingDetection`.
    public var needsProviderChoice: Bool { report != nil && cardProviders.isEmpty && !isAwaitingDetection }

    /// Some provider's effective state is still pending (detection not known yet).
    public var isAwaitingDetection: Bool {
        report?.providers.contains { $0.trackingMode != .off && $0.effectiveTracking == .pending } ?? false
    }

    public func canMoveCard(_ id: String, up: Bool) -> Bool {
        let visible = cardProviders.map(\.id)
        guard let index = visible.firstIndex(of: id) else { return false }
        return up ? index > 0 : index < visible.count - 1
    }

    public func moveCardUp(_ id: String) { moveCard(id, by: -1) }
    public func moveCardDown(_ id: String) { moveCard(id, by: 1) }

    /// Swaps with the adjacent visible card; the first manual move selects the custom order.
    private func moveCard(_ id: String, by offset: Int) {
        let visible = cardProviders.map(\.id)
        guard let index = visible.firstIndex(of: id), visible.indices.contains(index + offset) else { return }
        let neighbour = visible[index + offset]
        var full = CardOrdering.merged(cardOrderMode == .custom ? customCardOrder : orderedProviders.map(\.id),
                                       with: report?.providers ?? [])
        guard let a = full.firstIndex(of: id), let b = full.firstIndex(of: neighbour) else { return }
        full.swapAt(a, b)
        customCardOrder = full
        cardOrderMode = .custom
        preferences.set(full, forKey: PreferenceKey.customCardOrder)
        preferences.set(CardOrderMode.custom.rawValue, forKey: PreferenceKey.cardOrderMode)
    }

    /// "Use recent activity order". The saved custom order is kept for nothing else; recent applies now.
    public func useRecentActivityOrder() {
        cardOrderMode = .recent
        preferences.set(CardOrderMode.recent.rawValue, forKey: PreferenceKey.cardOrderMode)
        snapshotRecentOrder()
    }

    private func snapshotRecentOrder() {
        guard let providers = report?.providers else { return }
        recentOrderSnapshot = CardOrdering.recent(providers)
    }

    public var detectionNotices: [DetectionNotice] {
        guard let report else { return [] }
        return DetectionNotice.notices(report, acknowledged: acknowledgedDetections)
    }

    /// Keep history (or opening settings): hides the notice until its condition changes.
    public func acknowledgeDetection(_ notice: DetectionNotice) {
        acknowledgedDetections.insert(notice.acknowledgementKey)
        preferences.set(acknowledgedDetections.sorted(), forKey: PreferenceKey.acknowledgedDetections)
    }

    /// A stable key for the set of things that make the result indicator a warning:
    /// each active provider that is not fresh (with its health), each non-limit notice,
    /// each detection notice and a refresh error. Empty when nothing is wrong.
    public static func warningSignature(report: Report?, now: Date, detections: [DetectionNotice],
                                        refreshError: String?) -> String {
        var parts: [String] = []
        if let report {
            for provider in report.providers where MonitoringPage.isActive(provider) {
                let health = MonitoringPage.health(provider)
                if health != .fresh { parts.append("health:\(provider.id):\(health.rawValue)") }
            }
            parts += Presentation.notices(report, now: now).filter { $0.tone != .limit }.map { "notice:\($0.id)" }
        }
        parts += detections.map { "detection:\($0.acknowledgementKey)" }
        if refreshError != nil { parts.append("refresh") }
        return Set(parts).sorted().joined(separator: "|")
    }

    public var currentWarningSignature: String {
        Self.warningSignature(report: report, now: now, detections: detectionNotices,
                              refreshError: report == nil ? nil : refreshError)
    }

    /// The user has seen exactly the current warnings (opened the result popover or
    /// Monitoring), so the indicator stays neutral until the set changes.
    public var warningsAcknowledged: Bool {
        let current = currentWarningSignature
        return !current.isEmpty && current == acknowledgedWarnings
    }

    /// An acknowledgement holds only while the live set stays that set: once it becomes
    /// anything else (healthy included), it is forgotten, so a returning set warns again.
    private func clearStaleWarningAcknowledgement() {
        guard let acknowledged = acknowledgedWarnings, acknowledged != currentWarningSignature else { return }
        acknowledgedWarnings = nil
        preferences.set(nil as String?, forKey: PreferenceKey.acknowledgedWarnings)
    }

    /// Viewing the result popover or Monitoring acknowledges the warnings shown there.
    public func acknowledgeWarnings() {
        let current = currentWarningSignature
        guard current != acknowledgedWarnings else { return }
        acknowledgedWarnings = current
        preferences.set(current, forKey: PreferenceKey.acknowledgedWarnings)
    }

    private func pruneDetectionAcknowledgements() {
        guard let report, !acknowledgedDetections.isEmpty else { return }
        let kept = DetectionNotice.pruned(acknowledgedDetections, for: report)
        guard kept != acknowledgedDetections else { return }
        acknowledgedDetections = kept
        preferences.set(kept.sorted(), forKey: PreferenceKey.acknowledgedDetections)
    }

    /// Current Collect now progress, when the installed collector reports it.
    public var collectProgress: CollectProgress? {
        if case let .running(_, progress) = collect { return progress }
        return nil
    }

    private func loadCache() {
        guard snapshot == nil, let cacheURL, let data = try? Data(contentsOf: cacheURL),
              let cached = try? Report.decode(data) else { return }
        apply(cached, cached: true)
    }

    private func saveCache(_ report: Report) {
        guard let cacheURL else { return }
        do {
            try FileManager.default.createDirectory(at: cacheURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try report.encoded().write(to: cacheURL, options: .atomic)
        } catch {
            // The cache is a convenience; a failed write only slows the next launch.
        }
    }
}

/// Size and modification time of the collector's files; a change means a scan is worthwhile.
public struct SourceFingerprint: Equatable, Sendable {
    public var entries: [String: [Double]]

    public init(entries: [String: [Double]]) { self.entries = entries }

    public static func of(_ urls: [URL]) -> SourceFingerprint {
        var entries: [String: [Double]] = [:]
        for url in urls {
            let attributes = try? FileManager.default.attributesOfItem(atPath: url.path)
            let size = (attributes?[.size] as? NSNumber)?.doubleValue ?? -1
            let modified = (attributes?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? -1
            entries[url.path] = [size, modified]
        }
        return SourceFingerprint(entries: entries)
    }
}

struct StaticReport: ReportFetching {
    var report: Report
    func fetch() async throws -> Report { report }
}

struct NoCollector: CollectorRunning {
    func collectOnce(progressSupported: Bool, onProgress: @escaping @Sendable (CollectProgress) -> Void) async throws -> CollectRun {
        CollectRun(outcome: .failed, message: "Collect now isn’t available in previews.", finishedAt: Date())
    }
}

/// A service status and when it was observed (probe start time).
public struct ServiceObservation: Equatable, Sendable {
    public var status: ServiceStatus
    public var observedAt: Date
}
