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
}

final class ReentrantConfig: ConfigWriting, @unchecked Sendable {
    var applied: [[ConfigChange]] = []
    var duringFirst: (@MainActor () async -> Void)?
    func apply(_ changes: [ConfigChange]) async throws {
        applied.append(changes)
        if let hook = duringFirst { duringFirst = nil; await hook() }
    }
}
