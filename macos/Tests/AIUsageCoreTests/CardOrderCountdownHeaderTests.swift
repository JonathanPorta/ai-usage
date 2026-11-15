import Foundation
import XCTest
@testable import AIUsageCore

@MainActor
final class CardOrderTests: XCTestCase {
    /// codex newest, then grok, claude; antigravity and gemini_cli unknown.
    private func ordered() throws -> Report {
        var report = try legacyBase()
        let generated = report.generatedAt
        report.set("codex") { $0.lastActivityAt = generated.addingTimeInterval(-60) }
        report.set("grok") { $0.lastActivityAt = generated.addingTimeInterval(-300) }
        report.set("claude") { $0.lastActivityAt = generated.addingTimeInterval(-540) }
        return report
    }

    private func store(_ report: Report, _ preferences: PreferencesStoring) -> AppStore {
        let store = AppStore(environment: environment(), reporter: StaticReport(report: report), collector: NoCollector(),
                             clock: { report.generatedAt }, preferences: preferences)
        store.apply(report, cached: false)
        return store
    }

    func testRecentOrderUnknownLastStableTies() throws {
        var report = try ordered()
        let names = Dictionary(uniqueKeysWithValues: report.providers.map { ($0.id, $0.name) })
        let unknown = ["antigravity", "gemini_cli"].sorted { (names[$0]!, $0) < (names[$1]!, $1) }
        XCTAssertEqual(CardOrdering.recent(report.providers), ["codex", "grok", "claude"] + unknown)
        // Equal times tie by name then id, regardless of input order.
        let same = report.generatedAt
        report = report.adding(instance: "claude-az", copying: "claude", name: report.provider("claude")!.name)
        report.set("claude") { $0.lastActivityAt = same }
        report.set("claude-az") { $0.lastActivityAt = same }
        let forward = CardOrdering.recent(report.providers)
        let backward = CardOrdering.recent(report.providers.reversed())
        XCTAssertEqual(forward, backward)
        XCTAssertEqual(Array(forward.prefix(2)), ["claude", "claude-az"])
    }

    func testRefreshDoesNotReorderUntilNextOpen() throws {
        let report = try ordered()
        let subject = store(report, InMemoryPreferences())
        subject.popoverOpened()
        let before = subject.cardProviders.map(\.id)
        XCTAssertEqual(before.first, "codex")
        var newer = report
        let generated = report.generatedAt
        newer.set("claude") { $0.lastActivityAt = generated }
        subject.apply(newer, cached: false)
        XCTAssertEqual(subject.cardProviders.map(\.id), before, "a data refresh never reorders")
        subject.popoverOpened()
        XCTAssertEqual(subject.cardProviders.first?.id, "claude", "recomputed on the next open")
    }

    func testCustomOrderMovesPersistsAndReturnsToRecent() throws {
        let report = try ordered()
        let preferences = InMemoryPreferences()
        let subject = store(report, preferences)
        XCTAssertEqual(subject.cardOrderMode, .recent)
        XCTAssertFalse(subject.canMoveCard("codex", up: true))
        subject.moveCardDown("codex")
        XCTAssertEqual(subject.cardOrderMode, .custom, "the first manual move selects custom")
        XCTAssertEqual(Array(subject.cardProviders.map(\.id).prefix(2)), ["grok", "codex"])
        subject.moveCardUp("claude")
        XCTAssertEqual(Array(subject.cardProviders.map(\.id).prefix(3)), ["grok", "claude", "codex"])
        let custom = subject.cardProviders.map(\.id)

        // Restart keeps it.
        let restarted = store(report, preferences)
        XCTAssertEqual(restarted.cardOrderMode, .custom)
        XCTAssertEqual(restarted.cardProviders.map(\.id), custom)

        // Disable then re-enable keeps the slot.
        var disabled = report
        disabled.set("claude", policy(.off, .inactive, enabled: false))
        restarted.apply(disabled, cached: false)
        XCTAssertEqual(restarted.cardProviders.map(\.id), custom.filter { $0 != "claude" }, "disable applies immediately")
        restarted.moveCardDown("grok")  // reorder while hidden
        restarted.apply(report, cached: false)
        XCTAssertEqual(Array(restarted.cardProviders.map(\.id).prefix(3)), ["codex", "claude", "grok"])
        XCTAssertEqual(restarted.orderedProviders.map(\.id).firstIndex(of: "claude"), custom.firstIndex(of: "claude"),
                       "a hidden provider keeps its position")

        // New instances append at the end.
        let grown = report.adding(instance: "claude-az", copying: "claude", name: "Claude AZ")
        restarted.apply(grown, cached: false)
        XCTAssertEqual(restarted.cardProviders.last?.id, "claude-az")

        // Back to recent.
        restarted.useRecentActivityOrder()
        XCTAssertEqual(restarted.cardOrderMode, .recent)
        XCTAssertEqual(restarted.cardProviders.first?.id, "codex")
        XCTAssertEqual(preferences.string(forKey: PreferenceKey.cardOrderMode), "recent")
    }

    func testKeyboardListEqualsCardList() throws {
        var report = try ordered()
        report.set("grok", policy(.off, .inactive, enabled: false))
        let subject = store(report, InMemoryPreferences())
        XCTAssertEqual(subject.cardProviders.map(\.id), subject.orderedProviders.filter(\.isOverviewVisible).map(\.id))
        XCTAssertFalse(subject.cardProviders.contains { $0.id == "grok" })
    }
}

final class ResetCountdownTests: XCTestCase {
    let now = ReportDate.parse("2026-11-15T12:00:00Z")!

    private func text(_ seconds: TimeInterval) -> String {
        Format.resetCountdown(now.addingTimeInterval(seconds), now: now).text
    }

    func testBoundaries() {
        let h = 3600.0, m = 60.0
        XCTAssertEqual(text(7 * 24 * h), "Resets in 7 days")
        XCTAssertEqual(text(48 * h), "Resets in 2 days")
        XCTAssertEqual(text(47 * h), "Resets in 1 day")
        XCTAssertEqual(text(24 * h), "Resets in 1 day")
        XCTAssertEqual(text(23 * h + 59 * m), "Resets in 23 hours")
        XCTAssertEqual(text(2 * h), "Resets in 2 hours")
        XCTAssertEqual(text(1 * h), "Resets in 1 hour")
        XCTAssertEqual(text(59 * m), "Resets in 59 minutes")
        XCTAssertEqual(text(1 * m), "Resets in 1 minute")
        XCTAssertEqual(text(59), "Resets in less than a minute")
        XCTAssertEqual(text(0), "Reset due · awaiting update")
        XCTAssertEqual(text(-300), "Reset due · awaiting update")
        XCTAssertEqual(Format.resetCountdown(nil, now: now), ResetCountdown(text: "Reset time unavailable", exact: nil))
    }

    func testDSTCrossingUsesElapsedTime() throws {
        // US DST ended 2026-11-01 02:00 local: 23 real hours spans a calendar "day" in New York.
        let zone = try XCTUnwrap(TimeZone(identifier: "America/New_York"))
        let start = ReportDate.parse("2026-10-31T12:00:00Z")!
        let reset = start.addingTimeInterval(23 * 3600)
        XCTAssertEqual(Format.resetCountdown(reset, now: start, timeZone: zone).text, "Resets in 23 hours")
        let day = start.addingTimeInterval(24 * 3600)
        XCTAssertEqual(Format.resetCountdown(day, now: start, timeZone: zone).text, "Resets in 1 day")
    }

    func testExactText() throws {
        let zone = try XCTUnwrap(TimeZone(identifier: "Europe/Berlin"))
        let reset = ReportDate.parse("2026-11-20T08:30:00Z")!
        let exact = try XCTUnwrap(Format.resetCountdown(reset, now: now, timeZone: zone,
                                                        locale: Locale(identifier: "en_GB")).exact)
        XCTAssertTrue(exact.contains("Friday"), exact)
        XCTAssertTrue(exact.contains("20 November 2026"), exact)
        XCTAssertTrue(exact.contains("09:30"), exact)
        XCTAssertTrue(exact.contains("CET"), exact)
    }
}

final class HeaderFactsTests: XCTestCase {
    private func base() throws -> Report {
        var report = try legacyBase()
        report.service.state = .running
        report.schedule.state = "scheduled"
        report.collection.lastAttemptAt = report.generatedAt.addingTimeInterval(-180)
        report.schedule.nextScheduledAt = report.generatedAt.addingTimeInterval(52 * 60)
        return report
    }

    func testMonitoringStates() throws {
        let report = try base()
        let now = report.generatedAt
        let monitoring = HeaderFacts.monitoring(report, collecting: false, now: now)
        XCTAssertEqual(monitoring.state, .monitoring)
        XCTAssertEqual(monitoring.text, "Monitoring · checked 3 minutes ago · next check in 52 minutes")
        XCTAssertFalse(monitoring.symbol.isEmpty)
        XCTAssertFalse(monitoring.tooltip.isEmpty)

        var paused = report
        paused.schedule.state = "paused"
        let pausedFact = HeaderFacts.monitoring(paused, collecting: false, now: now)
        XCTAssertEqual(pausedFact.state, .paused)
        XCTAssertFalse(pausedFact.text.contains("next"), pausedFact.text)

        var stopped = report
        stopped.service.state = .stopped
        XCTAssertEqual(HeaderFacts.monitoring(stopped, collecting: false, now: now).state, .stopped)
        XCTAssertEqual(HeaderFacts.monitoring(report, collecting: true, now: now).state, .checking)
        var first = report
        first.collection.lastAttemptAt = nil
        XCTAssertEqual(HeaderFacts.monitoring(first, collecting: false, now: now).state, .waitingFirstRun)
        var missing = report
        missing.service.state = .notInstalled
        XCTAssertEqual(HeaderFacts.monitoring(missing, collecting: false, now: now).state, .notInstalled)
        XCTAssertEqual(HeaderFacts.monitoring(nil, collecting: false, now: now).state, .unavailable)
    }

    private func summary(_ report: inout Report, expected: Int?, fresh: Int?, stale: Int?, partly: Int = 0, failed: Int = 0) {
        report.collection.summary = CollectionState.Summary(
            providersAttempted: expected ?? 4, providersUpdated: (fresh ?? 0) + (stale ?? 0), providersPartly: partly,
            providersFailed: failed, skipped: [], providersExpected: expected, providersFresh: fresh, providersStale: stale)
    }

    func testResults() throws {
        var report = try base()
        // Four active providers with healthy sources; the fifth (Gemini) is off.
        for i in report.providers.indices {
            for j in report.providers[i].sources.indices { report.providers[i].sources[j].status = .ok }
        }
        report.set("gemini_cli") { $0.enabled = false }
        var none = report
        for i in none.providers.indices { none.providers[i].enabled = false }
        summary(&none, expected: 0, fresh: 0, stale: 0)
        XCTAssertEqual(HeaderFacts.result(none).kind, .noActiveProviders)

        summary(&report, expected: 4, fresh: 4, stale: 0)
        let fresh = HeaderFacts.result(report)
        XCTAssertEqual(fresh.kind, .success)
        XCTAssertEqual(fresh.severity, .ok)
        XCTAssertEqual(fresh.text, "4 of 4 active providers updated")

        // A stale source can never be counted as updated, whatever the summary claims.
        report.set("claude") { $0.sources[0].status = .stale }
        let stale = HeaderFacts.result(report)
        XCTAssertEqual(stale.kind, .attention)
        XCTAssertEqual(stale.text, "4 providers checked")
        XCTAssertEqual(stale.detail, "3 fresh · 1 stale")
        XCTAssertFalse(stale.text.contains("updated"))

        report.set("grok") { $0.sources[0].status = .failed }
        report.set("codex") { $0.sources[0].status = .notDetected }
        let partly = HeaderFacts.result(report)
        XCTAssertEqual(partly.kind, .attention)
        XCTAssertEqual(partly.detail, "1 fresh · 1 stale · 1 missing · 1 failed")

        for id in ["claude", "codex", "antigravity"] { report.set(id) { $0.sources[0].status = .failed } }
        XCTAssertEqual(HeaderFacts.result(report).kind, .failed)
        XCTAssertEqual(HeaderFacts.result(report).severity, .critical)

        var legacy = try base()
        legacy.collection.lastAttemptResult = .success
        legacy.collection.summary = CollectionState.Summary(providersAttempted: 4, providersUpdated: 4, providersPartly: 0,
                                                            providersFailed: 0, skipped: [])
        let old = HeaderFacts.result(legacy)
        XCTAssertEqual(old.text, "4 providers checked")
        XCTAssertEqual(old.kind, .success)

        var empty = try base()
        empty.collection.lastAttemptAt = nil
        empty.collection.summary = nil
        XCTAssertEqual(HeaderFacts.result(empty).kind, .none)
    }

    func testPausedAfterSuccessAndMonitoringWithPartialFailureCoexist() throws {
        var paused = try base()
        paused.schedule.state = "paused"
        for i in paused.providers.indices {
            for j in paused.providers[i].sources.indices { paused.providers[i].sources[j].status = .ok }
        }
        summary(&paused, expected: 5, fresh: 5, stale: 0)
        XCTAssertEqual(HeaderFacts.monitoring(paused, collecting: false, now: paused.generatedAt).state, .paused)
        XCTAssertEqual(HeaderFacts.result(paused).kind, .success)

        var running = try base()
        summary(&running, expected: 3, fresh: 2, stale: 0, failed: 1)
        XCTAssertEqual(HeaderFacts.monitoring(running, collecting: false, now: running.generatedAt).state, .monitoring)
        XCTAssertEqual(HeaderFacts.result(running).kind, .attention)
    }
}
