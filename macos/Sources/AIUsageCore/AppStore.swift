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
        fingerprint: @escaping @Sendable ([URL]) -> SourceFingerprint = SourceFingerprint.of
    ) {
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
            cacheURL: environment.stateDirectory.appendingPathComponent("last-report.json")
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
        collect = .finished(run)
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

    public func applySettings(_ changes: [ConfigChange]) async {
        guard !changes.isEmpty else { return }
        await perform("Saving settings…", success: "Settings saved") { try await self.config.apply(changes) }
    }

    private func perform(_ running: String, success: String, _ body: @escaping () async throws -> Void) async {
        guard !isActing else { return }
        action = .running(running)
        do {
            try await body()
            action = .done(success)
        } catch {
            action = .failed((error as? LocalizedError)?.errorDescription ?? String(describing: error))
        }
        await probeService()
        await refresh()
    }

    public func dismissAction() {
        if case .running = action { return }
        action = .idle
    }

    // MARK: Internals

    /// Called on the main actor after every new snapshot (not for cache loads);
    /// the app uses it to plan notifications.
    public var onSnapshotChange: ((Report) -> Void)?

    func apply(_ newReport: Report, cached: Bool) {
        snapshot = newReport
        isCachedReport = cached
        phase = .ready
        revision += 1
        if !cached, let report { onSnapshotChange?(report) }
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
