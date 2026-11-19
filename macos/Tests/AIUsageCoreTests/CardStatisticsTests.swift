@testable import AIUsageCore
import XCTest

final class CardStatisticsTests: XCTestCase {
    private func fixture() throws -> Report { try Report.fixture() }

    func testTodayDistinguishesMissingZeroPartialAndNeverCollected() throws {
        var claude = try XCTUnwrap(try fixture().provider("claude"))
        XCTAssertEqual(CardStatistics.today(claude).state, .partial)
        XCTAssertEqual(CardStatistics.today(claude).note, "today so far")
        claude.today?.partial = false
        claude.today?.total = 0
        XCTAssertEqual(CardStatistics.today(claude), TodayFigure(state: .zero, value: "0", note: "measured · no usage today"))
        claude.today = nil
        XCTAssertEqual(CardStatistics.today(claude), TodayFigure(state: .missing, value: "—", note: "no reading today"))
        let gemini = try XCTUnwrap(try fixture().provider("gemini_cli"))
        XCTAssertEqual(CardStatistics.today(gemini).state, .neverCollected)
    }

    func testStatisticsUseRecordedTotalNeverLifetime() throws {
        let report = try fixture()
        let claude = try XCTUnwrap(report.provider("claude"))
        let stats = CardStatistics.statistics(claude, now: report.generatedAt)
        XCTAssertEqual(stats.totalLabel, "Recorded total")
        XCTAssertEqual(stats.basis, "Input + output tokens")
        XCTAssertEqual(stats.recordedTotal, Format.tokens(claude.days.filter(\.hasValue).reduce(0) { $0 + ($1.total ?? 0) }))
        XCTAssertTrue(stats.coverage.contains("–"), stats.coverage)
        XCTAssertNotNil(stats.cacheNote)
        XCTAssertNotEqual(stats.lastActivity, "Not reported")
        // Peak comes from complete (measured) days only.
        let peak = claude.days.suffix(7).filter { $0.state == .measured }.max { ($0.total ?? 0) < ($1.total ?? 0) }
        XCTAssertEqual(stats.peakValue, Format.tokens(peak?.total))
    }

    func testStatisticsForProviderWithoutDays() throws {
        let report = try fixture()
        let gemini = try XCTUnwrap(report.provider("gemini_cli"))
        let stats = CardStatistics.statistics(gemini, now: report.generatedAt)
        XCTAssertEqual(stats.recordedTotal, "—")
        XCTAssertEqual(stats.coverage, "No days recorded")
        XCTAssertNil(stats.peakValue)
        XCTAssertEqual(stats.lastActivity, "Not reported")
    }

    func testQuotaCaptionUsesCountdownAndNeverRestoresCapacity() throws {
        let report = try fixture()
        var window = try XCTUnwrap(report.provider("claude")?.quota.windows.first)
        window.staleCause = nil
        window.resetsAt = report.generatedAt.addingTimeInterval(3 * 3600 + 5)
        XCTAssertEqual(Presentation.quotaCaption(window, now: report.generatedAt).text, "Resets in 3 hours")
        XCTAssertEqual(Presentation.quotaReset(window, now: report.generatedAt)?.replacesCaption, true)
        window.staleCause = .resetPassed
        window.resetsAt = report.generatedAt.addingTimeInterval(-60)
        let percent = window.percent
        XCTAssertEqual(Presentation.quotaCaption(window, now: report.generatedAt).text, "Reset due · awaiting update")
        XCTAssertEqual(window.percent, percent)
        window.staleCause = nil
        window.resetsAt = nil
        XCTAssertTrue(Presentation.quotaCaption(window, now: report.generatedAt).text.hasPrefix("Reset time unavailable"))
        XCTAssertNil(Presentation.quotaReset(window, now: report.generatedAt))
    }

    func testRangeLabelHasNoPeakSentence() throws {
        let claude = try XCTUnwrap(try fixture().provider("claude"))
        XCTAssertEqual(CardStatistics.rangeLabel(claude, days: 7), "7 days · input + output")
    }

    func testCardIssueMarksCachedQuotaAfterAFailedCheck() throws {
        var report = try fixture()
        var failure = try XCTUnwrap(report.collection.failures.first)
        failure.provider = "claude"
        failure.message = "usage endpoint returned 503"
        report.collection.failures.append(failure)
        let claude = try XCTUnwrap(report.provider("claude"))
        let issue = try XCTUnwrap(Presentation.cardIssue(claude, in: report))
        XCTAssertEqual(issue.severity, .failure)
        XCTAssertTrue(issue.text.contains("cached") || issue.text.contains("last reading"), issue.text)
        // A failed window alone, with no failure record, still marks the card.
        var bare = try fixture()
        let i = try XCTUnwrap(bare.providers.firstIndex { $0.id == "antigravity" })
        bare.providers[i].quota.windows[0].staleCause = .failed
        bare.providers[i].quota.windows[0].status = .stale
        XCTAssertEqual(Presentation.cardIssue(bare.providers[i], in: bare)?.severity, .failure)
        // Grok's failed billing source in the fixture.
        XCTAssertEqual(Presentation.cardIssue(try XCTUnwrap(report.provider("grok")), in: report)?.severity, .failure)
    }

    func testResetUnderAFailedWindowReadsAsLastKnown() throws {
        let report = try fixture()
        var window = try XCTUnwrap(report.provider("claude")?.quota.windows.first)
        window.resetsAt = report.generatedAt.addingTimeInterval(4 * 86_400 + 60)
        for cause in [StaleCause.failed, .auth, .notCollected] {
            window.staleCause = cause
            let reset = try XCTUnwrap(Presentation.quotaReset(window, now: report.generatedAt))
            XCTAssertTrue(reset.lastKnown)
            XCTAssertEqual(reset.countdown.text, "Last known: resets in 4 days")
            XCTAssertTrue(Presentation.compactQuotaCaption(window, now: report.generatedAt).warning)
        }
        window.staleCause = nil
        XCTAssertEqual(Presentation.quotaReset(window, now: report.generatedAt)?.lastKnown, false)
    }

    func testCoverageKeepsMissingDaysSeparate() throws {
        let report = try fixture()
        for provider in report.providers {
            let stats = CardStatistics.statistics(provider, now: report.generatedAt)
            XCTAssertFalse(stats.coverage.contains("missing"), stats.coverage)
        }
    }
}
