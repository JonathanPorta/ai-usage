import Foundation
import XCTest
@testable import AIUsageCore

@MainActor
final class FrictionFixesTests: XCTestCase {
    private func store(_ report: Report, _ preferences: PreferencesStoring) -> AppStore {
        let store = AppStore(environment: environment(), reporter: StaticReport(report: report), collector: NoCollector(),
                             clock: { report.generatedAt }, preferences: preferences)
        store.apply(report, cached: false)
        return store
    }

    // MARK: Subscription price

    func testPriceTextParses() {
        XCTAssertEqual(ConfigChange.parsePrice(""), .some(nil))
        XCTAssertEqual(ConfigChange.parsePrice("  "), .some(nil))
        XCTAssertEqual(ConfigChange.parsePrice("20"), .some(20))
        XCTAssertEqual(ConfigChange.parsePrice(" 19.99 "), .some(19.99))
        XCTAssertEqual(ConfigChange.parsePrice("$20"), .some(20))
        XCTAssertNil(ConfigChange.parsePrice("-1"))
        XCTAssertNil(ConfigChange.parsePrice("abc"))
    }

    func testPriceChangeForBuiltInAndAddedAccount() throws {
        let report = try legacyBase().adding(instance: "claude-az", copying: "claude", name: "Claude (az)")
        let claude = report.provider("claude")!
        XCTAssertEqual(ConfigChange.pricing(claude, text: "20"), .monthlySubscription("claude", 20))
        XCTAssertEqual(ConfigChange.pricing(claude, text: "20")?.assignment,
                       "providers.claude.monthly_subscription_usd=20")
        let account = report.provider("claude-az")!
        XCTAssertEqual(ConfigChange.pricing(account, text: "123")?.assignment,
                       "instances.claude-az.monthly_subscription_usd=123")
        XCTAssertNil(ConfigChange.pricing(claude, text: "x"), "invalid text is never written")
    }

    func testUnchangedPriceIsNotRewritten() throws {
        var report = try legacyBase()
        report.set("claude") { $0.costs.subscription = .init(monthlyUsd: 20, source: "user", enteredByUser: true) }
        let claude = report.provider("claude")!
        XCTAssertNil(ConfigChange.pricing(claude, text: "20"))
        XCTAssertEqual(ConfigChange.pricing(claude, text: ""), .monthlySubscription("claude", nil))
    }

    // MARK: Warning acknowledgement

    private func staleReport(_ ids: [String]) throws -> Report {
        var report = try legacyBase()
        for id in ids {
            report.set(id) { p in
                if !p.sources.isEmpty { p.sources[0].status = .stale }
            }
        }
        return report
    }

    func testViewingAcknowledgesUntilTheSetChanges() throws {
        let preferences = InMemoryPreferences()
        let report = try staleReport(["claude"])
        let subject = store(report, preferences)
        XCTAssertFalse(subject.currentWarningSignature.isEmpty, "a stale source is a warning")
        XCTAssertFalse(subject.warningsAcknowledged)
        subject.acknowledgeWarnings()
        XCTAssertTrue(subject.warningsAcknowledged)
        // Persisted: a relaunch keeps it quiet.
        XCTAssertTrue(store(report, preferences).warningsAcknowledged)
        // A refresh with the same set stays acknowledged.
        subject.apply(report, cached: false)
        XCTAssertTrue(subject.warningsAcknowledged)
        // A new issue warns again.
        let worse = try staleReport(["claude", "codex"])
        subject.apply(worse, cached: false)
        XCTAssertFalse(subject.warningsAcknowledged)
    }

    func testNothingWrongHasAnEmptySignature() {
        XCTAssertEqual(AppStore.warningSignature(report: nil, now: Date(), detections: [], refreshError: nil), "")
        XCTAssertEqual(AppStore.warningSignature(report: nil, now: Date(), detections: [], refreshError: "x"), "refresh")
    }

    func testReturningWarningSetWarnsAgain() throws {
        let preferences = InMemoryPreferences()
        let stale = try staleReport(["claude"])
        let subject = store(stale, preferences)
        subject.acknowledgeWarnings()
        XCTAssertTrue(subject.warningsAcknowledged)
        var fresh = stale
        fresh.set("claude") { p in for i in p.sources.indices { p.sources[i].status = .ok } }
        subject.apply(fresh, cached: false)
        subject.apply(stale, cached: false)
        XCTAssertFalse(subject.warningsAcknowledged)
        XCTAssertFalse(store(stale, preferences).warningsAcknowledged, "cleared across relaunch too")
    }

    func testPriceSavedWhileAnotherWriteRunsIsAppliedAfterIt() async throws {
        let config = ReentrantConfig()
        let subject = AppStore(environment: environment(), reporter: SpyReporter([.success(try fixture())]),
                               collector: SpyCollector(), service: SpyService(), config: config)
        let price = ConfigChange.monthlySubscription("claude", 20)
        config.duringFirst = {
            XCTAssertTrue(subject.isActing)
            let result = await subject.applySettings([price])
            XCTAssertNil(result, "queued, not run, while another write is in progress")
        }
        await subject.applySettings([.pollIntervalSeconds(1800)])
        XCTAssertEqual(config.applied, [[.pollIntervalSeconds(1800)], [price]])
        XCTAssertEqual(subject.action, .done("Settings saved"))
    }

    func testNewerEditDuringTheRefreshWinsOverAnOlderQueuedOne() async throws {
        let config = ReentrantConfig()
        let reporter = HookReporter(try fixture())
        let subject = AppStore(environment: environment(), reporter: reporter,
                               collector: SpyCollector(), service: SpyService(), config: config)
        let older = ConfigChange.monthlySubscription("claude", 20)
        let newer = ConfigChange.monthlySubscription("claude", 30)
        config.duringFirst = { _ = await subject.applySettings([older]) }
        reporter.during = {
            XCTAssertFalse(subject.isActing, "the outcome is already published during the refresh")
            _ = await subject.applySettings([newer])
        }
        await subject.applySettings([.pollIntervalSeconds(1800)])
        XCTAssertEqual(config.applied, [[.pollIntervalSeconds(1800)], [newer]])
        XCTAssertEqual(config.applied.last?.last, newer, "the latest edit is the stored value")
    }

    func testRecoveredRefreshErrorThatReturnsWarnsAgain() async throws {
        let stale = try staleReport(["claude"])
        let reporter = SpyReporter([.success(stale), .failure(Boom()), .success(stale), .failure(Boom())])
        let subject = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector(),
                               clock: { stale.generatedAt }, preferences: InMemoryPreferences())
        await subject.refresh()
        await subject.refresh()
        XCTAssertNotNil(subject.refreshError)
        subject.acknowledgeWarnings()
        XCTAssertTrue(subject.warningsAcknowledged)
        await subject.refresh()
        XCTAssertNil(subject.refreshError)
        await subject.refresh()
        XCTAssertNotNil(subject.refreshError)
        XCTAssertFalse(subject.warningsAcknowledged)
    }

    private func pricedStore(_ config: ConfigWriting) async throws -> AppStore {
        var report = try fixture()
        report.set("claude") { $0.costs.subscription = .init(monthlyUsd: 20, source: "user", enteredByUser: true) }
        let subject = AppStore(environment: environment(), reporter: SpyReporter([.success(report)]),
                               collector: SpyCollector(), service: SpyService(), config: config)
        await subject.refresh()
        return subject
    }

    private func storedClaudePrice(_ applied: [[ConfigChange]]) -> Double?? {
        for change in applied.flatMap({ $0 }).reversed() {
            if case let .monthlySubscription("claude", value) = change { return .some(value) }
        }
        return nil
    }

    func testRevertToReportedPriceReplacesAQueuedEdit() async throws {
        let config = ReentrantConfig()
        let subject = try await pricedStore(config)
        config.duringFirst = {
            XCTAssertNil(subject.savePrices(["claude": "30"]), "queued behind the running write")
            XCTAssertNil(subject.savePrices(["claude": "20"]), "back to the reported price")
        }
        await subject.applySettings([.pollIntervalSeconds(1800)])
        XCTAssertEqual(config.applied, [[.pollIntervalSeconds(1800)]], "the queued 30 is never written")
        XCTAssertNil(storedClaudePrice(config.applied), "stored price stays the reported 20")
    }

    func testRevertWhileThatPriceIsBeingWrittenIsWrittenAfterIt() async throws {
        let config = ReentrantConfig()
        let subject = try await pricedStore(config)
        config.duringFirst = { _ = subject.savePrices(["claude": "20"]) }
        await subject.savePrices(["claude": "30"])?.value
        XCTAssertEqual(storedClaudePrice(config.applied), .some(20))
        XCTAssertEqual(config.applied, [[.monthlySubscription("claude", 30)], [.monthlySubscription("claude", 20)]])
    }

    func testLastFieldValuesWinForTwoProvidersAndEmptyMeansNone() async throws {
        let config = ReentrantConfig()
        let subject = try await pricedStore(config)
        config.duringFirst = {
            _ = subject.savePrices(["claude": "25", "codex": "abc"])
            _ = subject.savePrices(["claude": "", "codex": "15"])
        }
        await subject.applySettings([.pollIntervalSeconds(1800)])
        XCTAssertEqual(config.applied, [[.pollIntervalSeconds(1800)],
                                        [.monthlySubscription("claude", nil), .monthlySubscription("codex", 15)]])
    }

    // MARK: Action banner lifetime

    func testDoneClearsItselfAndFailureStaysUntilSeenAndLeft() async throws {
        let config = SpyConfig()
        let store = AppStore(environment: environment(), reporter: SpyReporter([.success(try fixture())]),
                             collector: SpyCollector(), config: config)
        store.actionDoneDisplay = .milliseconds(50)
        await store.applySettings([.pollIntervalSeconds(1800)])
        XCTAssertEqual(store.action, .done("Settings saved"))
        await store.actionDismissal?.value
        XCTAssertEqual(store.action, .idle, "a routine result doesn't linger on later screens")

        config.error = ClientError.processFailed("refused")
        await store.applySettings([.pollIntervalSeconds(10)])
        XCTAssertEqual(store.action, .failed("refused"))
        XCTAssertNil(store.actionDismissal, "a failure is never cleared on a timer")
        store.screenChanged()
        XCTAssertEqual(store.action, .failed("refused"), "not yet shown: a screen change keeps it")
        store.actionBannerShown()
        store.screenChanged()
        XCTAssertEqual(store.action, .idle, "seen, then left: cleared")

        await store.applySettings([.pollIntervalSeconds(10)])
        store.actionBannerShown()
        config.error = nil
        store.actionDoneDisplay = .seconds(60)
        await store.applySettings([.pollIntervalSeconds(1800)])
        XCTAssertEqual(store.action, .done("Settings saved"), "a later success replaces the failure")
        store.screenChanged()
        XCTAssertEqual(store.action, .done("Settings saved"), "only failures clear on a screen change")
        // A newer failure after a done cancels that done's timer and resets "seen".
        config.error = ClientError.processFailed("again")
        await store.applySettings([.pollIntervalSeconds(10)])
        XCTAssertNil(store.actionDismissal)
        store.screenChanged()
        XCTAssertEqual(store.action, .failed("again"))
    }
    // MARK: History "Tracking is off"

    func testAutoNotDetectedIsNotTrackingOff() throws {
        var p = try legacyBase().providers[0]
        p.tracking = .auto
        p.trackingEffective = .inactive
        p.enabled = false
        p.setup = .notDetected
        XCTAssertFalse(DetailPresentation.isTrackingOff(p), "Auto, not found: not off, so no \"Turn on tracking\" over Auto")
        p.tracking = .off
        XCTAssertTrue(DetailPresentation.isTrackingOff(p))
        p.tracking = nil
        p.setup = .disabled
        XCTAssertTrue(DetailPresentation.isTrackingOff(p), "legacy collector: disabled is off")
    }
}

final class HookReporter: ReportFetching, @unchecked Sendable {
    let report: Report
    var during: (@MainActor () async -> Void)?
    init(_ report: Report) { self.report = report }
    func fetch() async throws -> Report {
        if let hook = during { during = nil; await hook() }
        return report
    }
}

final class ReentrantConfig: ConfigWriting, @unchecked Sendable {
    var applied: [[ConfigChange]] = []
    var duringFirst: (@MainActor () async -> Void)?
    func apply(_ changes: [ConfigChange]) async throws {
        applied.append(changes)
        if let hook = duringFirst { duringFirst = nil; await hook() }
    }
}
