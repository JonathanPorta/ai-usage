import Foundation
import Observation
import XCTest
@testable import AIUsageCore

// MARK: - Test doubles

final class SpyReporter: ReportFetching, @unchecked Sendable {
    private let lock = NSLock()
    private var queue: [Result<Report, Error>]
    private(set) var calls = 0

    init(_ results: [Result<Report, Error>]) { queue = results }

    func fetch() async throws -> Report {
        lock.lock()
        calls += 1
        let next = queue.count > 1 ? queue.removeFirst() : queue[0]
        lock.unlock()
        return try next.get()
    }
}

final class SpyCollector: CollectorRunning, @unchecked Sendable {
    private(set) var calls = 0
    private(set) var progressRequested: [Bool] = []
    var outcome: CollectRun.Outcome = .complete
    var gate: (() async -> Void)?
    var progress: [CollectProgress] = []

    func collectOnce(progressSupported: Bool, onProgress: @escaping @Sendable (CollectProgress) -> Void) async throws -> CollectRun {
        calls += 1
        progressRequested.append(progressSupported)
        for event in progress { onProgress(event) }
        if let gate { await gate() }
        return CollectRun(outcome: outcome, message: "Appended 10 rows", finishedAt: Date())
    }
}

/// A reporter whose runs can be held open, to test overlapping refreshes.
@MainActor
final class GatedReporter: ReportFetching, @unchecked Sendable {
    var results: [Report]
    private(set) var calls = 0
    var gates: [CheckedContinuation<Void, Never>] = []
    var holdRuns = false

    init(_ results: [Report]) { self.results = results }

    nonisolated func fetch() async throws -> Report {
        await MainActor.run { self.calls += 1 }
        if await MainActor.run(body: { self.holdRuns }) {
            await withCheckedContinuation { continuation in
                Task { @MainActor in self.gates.append(continuation) }
            }
        }
        return await MainActor.run { self.results.count > 1 ? self.results.removeFirst() : self.results[0] }
    }

    func releaseAll() {
        let pending = gates
        gates = []
        pending.forEach { $0.resume() }
    }
}

final class SpyService: ServiceControlling, @unchecked Sendable {
    var calls: [String] = []
    var state: Service.State = .running
    func status() async throws -> ServiceStatus {
        calls.append("status")
        return ServiceStatus(installed: true, state: state, pid: state == .running ? 7 : nil, disabled: state != .running, detail: "")
    }
    func start() async throws { calls.append("start"); state = .running }
    func stop() async throws { calls.append("stop"); state = .stopped }
}

final class SpyConfig: ConfigWriting, @unchecked Sendable {
    var applied: [[ConfigChange]] = []
    var error: Error?
    func apply(_ changes: [ConfigChange]) async throws {
        if let error { throw error }
        applied.append(changes)
    }
}

struct Boom: Error, LocalizedError { var errorDescription: String? { "report exploded" } }

func fixture() throws -> Report { try Report.fixture() }

func environment() -> CollectorEnvironment {
    CollectorEnvironment(python: URL(fileURLWithPath: "/usr/bin/python3"), collectorScript: nil,
                         configPath: URL(fileURLWithPath: "/sandbox/config.json"), reportScript: nil,
                         skipServiceProbe: true, stateDirectory: URL(fileURLWithPath: NSTemporaryDirectory()),
                         childEnvironment: [:])
}

func bumped(_ report: Report, minutes: Double) -> Report {
    var copy = report
    copy.generatedAt = report.generatedAt.addingTimeInterval(minutes * 60)
    copy.collection.lastAttemptAt = copy.generatedAt
    return copy
}

// MARK: - Contract

final class ReportDecodingTests: XCTestCase {
    func testFixtureDecodesWithEveryProviderAndDayState() throws {
        let report = try fixture()
        XCTAssertEqual(report.schema, Report.supportedSchema)
        XCTAssertEqual(report.providers.map(\.id), ["codex", "claude", "antigravity", "grok", "gemini_cli"])
        let states = Set(report.providers.flatMap { $0.days.map(\.state) })
        XCTAssertEqual(states, [.measured, .zero, .missing, .notCollected, .partial])
        XCTAssertEqual(report.provider("codex")?.quota.status, .mixed)
        XCTAssertEqual(report.provider("grok")?.quota.windows.first?.staleCause, .failed)
        XCTAssertNil(report.provider("codex")?.today?.input, "Codex has no input/output split")
    }

    func testUnknownSchemaIsRejected() {
        let data = Data(#"{"schema":"ai-usage/report/v2"}"#.utf8)
        XCTAssertThrowsError(try Report.decode(data)) { error in
            XCTAssertEqual(error as? ReportError, .unsupportedSchema("ai-usage/report/v2"))
        }
    }

    func testRoundTripThroughTheCacheEncoding() throws {
        let report = try fixture()
        XCTAssertEqual(try Report.decode(report.encoded()), report)
    }
}

// MARK: - Store

@MainActor
final class AppStoreTests: XCTestCase {
    func testPopoverOpenRendersCacheAndNeverCollects() async throws {
        let reporter = SpyReporter([.success(try fixture())])
        let collector = SpyCollector()
        let store = AppStore(environment: environment(), reporter: reporter, collector: collector)
        await store.refresh()
        let before = reporter.calls
        store.popoverOpened()  // collector files unchanged: no scan, never a collection
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(reporter.calls, before)
        XCTAssertEqual(collector.calls, 0, "opening the popover must not start a collection")
        XCTAssertNotNil(store.report)
    }

    func testCollectNowRefreshesSharedStoreSeenByEveryObserver() async throws {
        let base = try fixture()
        let after = bumped(base, minutes: 5)
        let reporter = SpyReporter([.success(base), .success(after)])
        let collector = SpyCollector()
        let store = AppStore(environment: environment(), reporter: reporter, collector: collector)
        await store.refresh()
        let startRevision = store.revision

        // Two independent observers stand in for the popover and an already-open History window.
        var popoverSaw = false
        var historySaw = false
        withObservationTracking({ _ = store.report }, onChange: { popoverSaw = true })
        withObservationTracking({ _ = store.revision }, onChange: { historySaw = true })

        let run = await store.collectNow()
        XCTAssertEqual(run?.outcome, .complete)
        XCTAssertEqual(collector.calls, 1)
        XCTAssertEqual(store.revision, startRevision + 1)
        XCTAssertEqual(store.report?.generatedAt, after.generatedAt)
        XCTAssertTrue(popoverSaw)
        XCTAssertTrue(historySaw)
        if case let .finished(result) = store.collect { XCTAssertEqual(result.outcome, .complete) } else { XCTFail("no result") }
    }

    func testOverlappingRefreshesCoalesceIntoOneFollowUpRun() async throws {
        let base = try fixture()
        let reporter = GatedReporter([bumped(base, minutes: 1), bumped(base, minutes: 2)])
        reporter.holdRuns = true
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector())
        let first = Task { await store.refresh() }
        for _ in 0..<200 where reporter.gates.isEmpty { try await Task.sleep(nanoseconds: 2_000_000) }
        // Three more requests while the first run is in flight.
        let others = (0..<3).map { _ in Task { await store.refresh() } }
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(reporter.calls, 1)
        reporter.holdRuns = false
        reporter.releaseAll()
        await first.value
        for task in others { await task.value }
        XCTAssertEqual(reporter.calls, 2, "requests made during a run share exactly one follow-up run")
        XCTAssertEqual(store.reportRuns, 2)
        XCTAssertEqual(store.snapshot?.generatedAt, bumped(base, minutes: 2).generatedAt)
        XCTAssertFalse(store.isRefreshing)
    }

    func testOlderResultNeverReplacesNewerSnapshot() async throws {
        let base = try fixture()
        let reporter = SpyReporter([.success(bumped(base, minutes: 10)), .success(bumped(base, minutes: 1))])
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector())
        await store.refresh()
        let revision = store.revision
        await store.refresh()
        XCTAssertEqual(store.snapshot?.generatedAt, bumped(base, minutes: 10).generatedAt)
        XCTAssertEqual(store.revision, revision, "a discarded older result must not notify observers")
    }

    func testReopeningThePopoverScansOnlyWhenSourcesChanged() async throws {
        let reporter = SpyReporter([.success(try fixture())])
        let marker = FingerprintBox()
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector(),
                             service: SpyService(), fingerprint: { _ in marker.value })
        await store.refresh()
        XCTAssertEqual(reporter.calls, 1)
        for _ in 0..<5 { await store.refreshIfSourcesChanged() }
        XCTAssertEqual(reporter.calls, 1, "unchanged collector files: no CSV scan on reopen")
        marker.value = SourceFingerprint(entries: ["/csv": [2, 2]])
        await store.refreshIfSourcesChanged()
        XCTAssertEqual(reporter.calls, 2)
    }

    func testCachedSnapshotCannotShowStaleReadingsAsCurrent() async throws {
        let report = try fixture()
        let grokUsageDeadline = try XCTUnwrap(report.provider("grok")?.usage.becomesStaleAt)
        let antigravity = try XCTUnwrap(report.provider("antigravity")?.quota.windows.first)
        XCTAssertEqual(antigravity.status, .current)
        let deadline = try XCTUnwrap(antigravity.becomesStaleAt)
        let later = max(deadline, grokUsageDeadline).addingTimeInterval(1)
        let store = AppStore.preview(report, clock: later)
        let shown = try XCTUnwrap(store.report)
        XCTAssertEqual(shown.provider("grok")?.usage.status, .stale)
        let window = try XCTUnwrap(shown.provider("antigravity")?.quota.windows.first)
        XCTAssertEqual(window.status, .stale)
        XCTAssertNil(window.limit)
        XCTAssertEqual(window.measuredAt, antigravity.measuredAt, "measurement times never change")
        XCTAssertEqual(store.snapshot, report, "the stored snapshot itself is untouched")
    }

    func testServiceAndSettingsActionsUseTheirClientsThenRefresh() async throws {
        let reporter = SpyReporter([.success(try fixture())])
        let service = SpyService()
        let config = SpyConfig()
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector(),
                             service: service, config: config)
        await store.stopService()
        XCTAssertEqual(service.calls, ["stop", "status"])
        XCTAssertEqual(store.report?.service.state, .stopped)
        XCTAssertEqual(store.action, .done("Collector stopped; it stays stopped at login"))
        await store.setPaused(true)
        await store.applySettings([.pollIntervalSeconds(1800), .monthlySubscription("codex", 20)])
        XCTAssertEqual(config.applied, [[.pollPaused(true)], [.pollIntervalSeconds(1800), .monthlySubscription("codex", 20)]])
        XCTAssertEqual(reporter.calls, 3, "each action refreshes the shared snapshot")
        config.error = ClientError.processFailed("poll_interval_seconds must be at least 60")
        await store.applySettings([.pollIntervalSeconds(10)])
        XCTAssertEqual(store.action, .failed("poll_interval_seconds must be at least 60"))
        XCTAssertNotNil(store.report, "a failed action keeps the last good snapshot")
    }

    func testCollectNowReportsProviderProgressWhenSupported() async throws {
        let reporter = SpyReporter([.success(try fixture())])
        let collector = SpyCollector()
        collector.progress = [CollectProgress(providerId: "codex", index: 1, total: 4)]
        var release: CheckedContinuation<Void, Never>?
        collector.gate = { await withCheckedContinuation { release = $0 } }
        let store = AppStore(environment: environment(), reporter: reporter, collector: collector)
        await store.refresh()
        let task = Task { await store.collectNow() }
        for _ in 0..<200 where release == nil { try await Task.sleep(nanoseconds: 2_000_000) }
        try await Task.sleep(nanoseconds: 20_000_000)
        if case let .running(_, progress) = store.collect {
            XCTAssertEqual(progress, CollectProgress(providerId: "codex", index: 1, total: 4))
        } else { XCTFail("not running") }
        XCTAssertEqual(collector.progressRequested, [true], "fixture models a 2.2.0 collector")
        release?.resume()
        _ = await task.value
    }

    func testCollectNowIsSingleFlight() async throws {
        let reporter = SpyReporter([.success(try fixture())])
        let collector = SpyCollector()
        var release: CheckedContinuation<Void, Never>?
        collector.gate = { await withCheckedContinuation { release = $0 } }
        let store = AppStore(environment: environment(), reporter: reporter, collector: collector)
        let first = Task { await store.collectNow() }
        for _ in 0..<100 where !store.isCollecting { try await Task.sleep(nanoseconds: 5_000_000) }
        XCTAssertTrue(store.isCollecting)
        let second = await store.collectNow()
        XCTAssertNil(second, "a second Collect now while one runs must be ignored")
        for _ in 0..<100 where release == nil { try await Task.sleep(nanoseconds: 5_000_000) }
        release?.resume()
        _ = await first.value
        XCTAssertEqual(collector.calls, 1)
        XCTAssertFalse(store.isCollecting)
    }

    func testFailedRefreshKeepsLastGoodReportVisible() async throws {
        let reporter = SpyReporter([.success(try fixture()), .failure(Boom())])
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector())
        await store.refresh()
        await store.refresh()
        XCTAssertEqual(store.phase, .ready)
        XCTAssertNotNil(store.report)
        XCTAssertEqual(store.refreshError, "report exploded")
    }

    func testFirstRefreshFailureIsShownAsFailedPhase() async {
        let store = AppStore(environment: environment(), reporter: SpyReporter([.failure(Boom())]), collector: SpyCollector())
        await store.refresh()
        XCTAssertEqual(store.phase, .failed("report exploded"))
    }

    func testCachedReportLoadsBeforeAnyRefresh() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let cache = directory.appendingPathComponent("last-report.json")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try fixture().encoded().write(to: cache)
        let reporter = SpyReporter([.failure(Boom())])
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector(), cacheURL: cache)
        store.start()
        XCTAssertNotNil(store.report, "the cached report must render immediately")
        XCTAssertTrue(store.isCachedReport)
    }
}

// MARK: - Presentation

final class FingerprintBox: @unchecked Sendable {
    var value = SourceFingerprint(entries: ["/csv": [1, 1]])
}

final class PresentationTests: XCTestCase {
    func testNotificationsFireOncePerLimitPeriodAndPerFailedCheck() throws {
        let report = try fixture()
        let now = report.generatedAt
        let prefs = NotificationPreferences(limits: true, failures: true)
        let first = NotificationPlanner.alerts(for: report, now: now, preferences: prefs, delivered: [])
        XCTAssertTrue(first.contains { $0.title == "Codex weekly limit reached" })
        XCTAssertTrue(first.contains { $0.title == "Grok Build check failed" })
        XCTAssertFalse(first.contains { $0.title.contains("5-hour") }, "stale windows never notify")
        let again = NotificationPlanner.alerts(for: report, now: now, preferences: prefs, delivered: Set(first.map(\.key)))
        XCTAssertTrue(again.isEmpty, "each alert is delivered once")
        let quiet = NotificationPlanner.alerts(for: report, now: now, preferences: .init(limits: false, failures: false), delivered: [])
        XCTAssertTrue(quiet.isEmpty)
        let late = NotificationPlanner.alerts(for: report, now: now.addingTimeInterval(3 * 3600),
                                              preferences: .init(limits: false, failures: true), delivered: [])
        XCTAssertTrue(late.isEmpty, "old failures don't notify")
    }

    func testPausedStatusKeepsCollectNowAvailable() throws {
        var report = try fixture()
        report.schedule.state = "paused"
        let status = Presentation.status(report, collecting: false, now: report.generatedAt)
        XCTAssertEqual(status.tone, .paused)
        XCTAssertTrue(status.detail.hasSuffix("Collect now still works"))
        report.collector.capabilities?.pause = false
        report.collector.installedVersion = "2.1.0"
        XCTAssertNotNil(MonitoringText.pauseUnavailable(report))
    }

    func testConfigAssignmentsMatchTheCollectorContract() {
        XCTAssertEqual(ConfigChange.pollPaused(true).assignment, "poll_paused=true")
        XCTAssertEqual(ConfigChange.pollIntervalSeconds(1800).assignment, "poll_interval_seconds=1800")
        XCTAssertEqual(ConfigChange.providerEnabled("gemini_cli", false).assignment, "providers.gemini_cli.enabled=false")
        XCTAssertEqual(ConfigChange.monthlySubscription("codex", 20).assignment, "providers.codex.monthly_subscription_usd=20")
        XCTAssertEqual(ConfigChange.monthlySubscription("codex", 19.5).assignment, "providers.codex.monthly_subscription_usd=19.5")
        XCTAssertEqual(ConfigChange.monthlySubscription("codex", nil).assignment, "providers.codex.monthly_subscription_usd=null")
    }

    func testTokenFormatting() {
        XCTAssertEqual(Format.tokens(776_000), "776K")
        XCTAssertEqual(Format.tokens(1_150_000), "1.15M")
        XCTAssertEqual(Format.tokens(20_100_000), "20.1M")
        XCTAssertEqual(Format.tokens(0), "0")
        XCTAssertEqual(Format.tokens(nil), "—")
        XCTAssertEqual(Format.percent(0.3), "<1%")
    }

    func testHealthyStatusIsOneQuietLine() throws {
        let report = try fixture()
        let now = report.generatedAt
        let status = Presentation.status(report, collecting: false, now: now)
        XCTAssertEqual(status.tone, .healthy)
        XCTAssertEqual(status.title, "Monitoring")
        XCTAssertEqual(status.detail, "Checked 23 min ago · next in 37 min")
    }

    func testCollectingAndStoppedStatus() throws {
        var report = try fixture()
        XCTAssertEqual(Presentation.status(report, collecting: true, now: report.generatedAt).tone, .collecting)
        report.service.state = .stopped
        XCTAssertEqual(Presentation.status(report, collecting: false, now: report.generatedAt).title, "Collector stopped")
    }

    func testNoticesPutFailuresFirstThenFreshLimits() throws {
        let report = try fixture()
        let notices = Presentation.notices(report, now: report.generatedAt)
        XCTAssertEqual(notices.first?.title, "Grok Build quota check failed")
        XCTAssertTrue(notices.contains { $0.title == "Codex weekly limit reached" },
                      "a current, exhausted weekly window is flagged even though the 5-hour window is stale")
        XCTAssertFalse(notices.contains { $0.title.contains("5-hour") }, "stale windows never raise limit notices")
    }

    func testStaleQuotaCaptionNamesTheReadingTime() throws {
        let report = try fixture()
        let five = try XCTUnwrap(report.provider("codex")?.quota.windows.first { $0.short == "5-hour" })
        let caption = Presentation.quotaCaption(five, now: report.generatedAt)
        XCTAssertTrue(caption.warning)
        XCTAssertTrue(caption.text.hasPrefix("Not reported since"), caption.text)
    }

    func testReadoutsDistinguishMissingZeroAndPartial() throws {
        let report = try fixture()
        let grok = try XCTUnwrap(report.provider("grok"))
        let missing = try XCTUnwrap(grok.days.first { $0.state == .missing })
        XCTAssertTrue(Presentation.readout(grok, day: missing, asOf: nil).hasSuffix("missing reading, not zero"))
        let zero = try XCTUnwrap(grok.days.first { $0.state == .zero })
        XCTAssertTrue(Presentation.readout(grok, day: zero, asOf: nil).contains("0 tokens — measured, no usage"))
        let today = try XCTUnwrap(grok.days.last)
        XCTAssertTrue(Presentation.readout(grok, day: today, asOf: grok.today?.asOf).hasPrefix("Today so far"))
    }
}

// MARK: - Isolation

final class RecordingRunner: ProcessRunning, @unchecked Sendable {
    var invocations: [[String]] = []
    func run(_ executable: URL, arguments: [String], environment: [String: String], timeout: TimeInterval) async throws -> ProcessResult {
        invocations.append(arguments)
        return ProcessResult(exitCode: 0, stdout: Data("{}".utf8), stderr: "", timedOut: false)
    }
}

final class SandboxIsolationTests: XCTestCase {
    func testSandboxEnvironmentNeverRunsServiceControl() async {
        var sandbox = environment()
        sandbox.skipServiceProbe = true
        sandbox.reportScript = URL(fileURLWithPath: "/repo/ai_usage_report.py")
        let runner = RecordingRunner()
        let control = LiveServiceControl(environment: sandbox, runner: runner)
        for action in ["status", "start", "stop"] {
            do {
                switch action {
                case "status": _ = try await control.status()
                case "start": try await control.start()
                default: try await control.stop()
                }
                XCTFail("\(action) must refuse in a sandbox environment")
            } catch {}
        }
        XCTAssertTrue(runner.invocations.isEmpty, "no launchd command may run for a sandbox data source")
    }
}

// MARK: - Environment

final class CollectorEnvironmentTests: XCTestCase {
    func testResolvesInterpreterScriptAndConfigFromLaunchAgentPlist() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let agents = home.appendingPathComponent("Library/LaunchAgents")
        try FileManager.default.createDirectory(at: agents, withIntermediateDirectories: true)
        let plist: [String: Any] = [
            "Label": CollectorEnvironment.serviceLabel,
            "ProgramArguments": ["/opt/py/bin/python3.14", "/h/.ai-usage/collector.py", "daemon", "--config", "/h/.ai-usage/config.json"],
            "EnvironmentVariables": ["PATH": "/opt/homebrew/bin:/usr/bin"],
        ]
        try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
            .write(to: agents.appendingPathComponent("codes.porta.ai-usage.plist"))
        let resolved = CollectorEnvironment.resolve(environment: [:], home: home, bundleResources: nil)
        XCTAssertEqual(resolved.python.path, "/opt/py/bin/python3.14")
        XCTAssertEqual(resolved.collectorScript?.path, "/h/.ai-usage/collector.py")
        XCTAssertEqual(resolved.configPath.path, "/h/.ai-usage/config.json")
        XCTAssertEqual(resolved.childEnvironment["PATH"], "/opt/homebrew/bin:/usr/bin")
        XCTAssertEqual(resolved.childEnvironment["PYTHONDONTWRITEBYTECODE"], "1")
    }

    func testEnvironmentOverridesWinForSandboxRuns() {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let resolved = CollectorEnvironment.resolve(
            environment: ["AI_USAGE_CONFIG": "/sandbox/config.json", "AI_USAGE_PYTHON": "/usr/bin/python3",
                          "AI_USAGE_SERVICE": "skip", "AI_USAGE_STATE_DIR": "/sandbox/state"],
            home: home, bundleResources: nil)
        XCTAssertEqual(resolved.configPath.path, "/sandbox/config.json")
        XCTAssertTrue(resolved.skipServiceProbe)
        XCTAssertEqual(resolved.stateDirectory.path, "/sandbox/state")
        XCTAssertNil(resolved.collectorScript, "no plist and no override: Collect now must report the collector missing")
    }
}
