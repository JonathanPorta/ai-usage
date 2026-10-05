import Foundation
import Observation

/// The one shared application store. Owned by the App and injected into the
/// MenuBarExtra popover and the History window, so both always render the same
/// report. Views read it; only refreshes and Collect now write to it.
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
        case running(startedAt: Date)
        case finished(CollectRun)
    }

    public private(set) var report: Report?
    public private(set) var phase: Phase = .loading
    /// Error from the most recent refresh, even when an older report is still shown.
    public private(set) var refreshError: String?
    public private(set) var isRefreshing = false
    public private(set) var lastRefreshAt: Date?
    public private(set) var collect: CollectState = .idle
    /// Increments on every report change; observers (e.g. History) can key on it.
    public private(set) var revision = 0
    /// Whether this report came from the on-disk cache rather than a live run.
    public private(set) var isCachedReport = false

    public let environment: CollectorEnvironment
    private let reporter: ReportFetching
    private let collector: CollectorRunning
    private let clock: @Sendable () -> Date
    private let cacheURL: URL?
    private var timer: Timer?

    public static let popoverRefreshAge: TimeInterval = 60
    public static let backgroundRefreshInterval: TimeInterval = 5 * 60

    public init(
        environment: CollectorEnvironment,
        reporter: ReportFetching,
        collector: CollectorRunning,
        clock: @escaping @Sendable () -> Date = Date.init,
        cacheURL: URL? = nil
    ) {
        self.environment = environment
        self.reporter = reporter
        self.collector = collector
        self.clock = clock
        self.cacheURL = cacheURL
    }

    public static func live() -> AppStore {
        let environment = CollectorEnvironment.resolve()
        return AppStore(
            environment: environment,
            reporter: LiveReportClient(environment: environment),
            collector: LiveCollectorClient(environment: environment),
            cacheURL: environment.stateDirectory.appendingPathComponent("last-report.json")
        )
    }

    /// A store pre-filled with a report, for previews and tests. Never used by the running app.
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

    public var now: Date { clock() }

    // MARK: Lifecycle

    /// Launch: show the cached report instantly, then refresh in the background.
    public func start() {
        loadCache()
        Task { await refresh() }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: Self.backgroundRefreshInterval, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refresh() }
        }
    }

    /// Popover opened: never collects. Re-reads the report only when it is old.
    public func popoverOpened() {
        guard !isRefreshing, !isCollecting else { return }
        if let last = lastRefreshAt, clock().timeIntervalSince(last) < Self.popoverRefreshAge { return }
        Task { await refresh() }
    }

    public func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let fresh = try await reporter.fetch()
            apply(fresh, cached: false)
            refreshError = nil
            saveCache(fresh)
        } catch {
            let message = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
            refreshError = message
            if report == nil { phase = .failed(message) }
        }
        lastRefreshAt = clock()
    }

    /// Collect now: the collector's existing one-time path, one run at a time,
    /// followed by a report refresh that every observing view receives.
    @discardableResult
    public func collectNow() async -> CollectRun? {
        guard !isCollecting else { return nil }
        collect = .running(startedAt: clock())
        let run: CollectRun
        do {
            run = try await collector.collectOnce()
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

    // MARK: Internals

    func apply(_ newReport: Report, cached: Bool) {
        report = newReport
        isCachedReport = cached
        phase = .ready
        revision += 1
    }

    private func loadCache() {
        guard report == nil, let cacheURL, let data = try? Data(contentsOf: cacheURL),
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

struct StaticReport: ReportFetching {
    var report: Report
    func fetch() async throws -> Report { report }
}

struct NoCollector: CollectorRunning {
    func collectOnce() async throws -> CollectRun {
        CollectRun(outcome: .failed, message: "Collect now isn’t available in previews.", finishedAt: Date())
    }
}
