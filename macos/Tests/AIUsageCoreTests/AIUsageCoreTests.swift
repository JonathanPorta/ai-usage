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
    private let lock = NSLock()
    private(set) var calls = 0
    var outcome: CollectRun.Outcome = .complete
    var gate: (() async -> Void)?

    func collectOnce() async throws -> CollectRun {
        lock.lock(); calls += 1; lock.unlock()
        if let gate { await gate() }
        return CollectRun(outcome: outcome, message: "Appended 10 rows", finishedAt: Date())
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
        store.popoverOpened()  // within 60 s of the last refresh: no work at all
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(reporter.calls, before)
        XCTAssertEqual(collector.calls, 0, "opening the popover must not start a collection")
        XCTAssertNotNil(store.report)
    }

    func testPopoverOpenAfterSixtySecondsRefreshesReportOnly() async throws {
        var clockNow = Date()
        let reporter = SpyReporter([.success(try fixture())])
        let collector = SpyCollector()
        let store = AppStore(environment: environment(), reporter: reporter, collector: collector,
                             clock: { clockNow })
        await store.refresh()
        clockNow = clockNow.addingTimeInterval(61)
        store.popoverOpened()
        for _ in 0..<50 where reporter.calls < 2 { try await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertEqual(reporter.calls, 2)
        XCTAssertEqual(collector.calls, 0)
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

final class PresentationTests: XCTestCase {
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
