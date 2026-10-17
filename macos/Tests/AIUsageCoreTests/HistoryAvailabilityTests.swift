import XCTest
@testable import AIUsageCore

/// The History window's empty states come from detection and configuration,
/// never from an empty dataset alone; measured zero is data.
final class HistoryAvailabilityTests: XCTestCase {
    private func provider(_ id: String) throws -> Provider {
        try XCTUnwrap(try fixture().provider(id))
    }

    /// Every day turned into a missing day: no history at all.
    private func withoutHistory(_ provider: Provider) -> Provider {
        var copy = provider
        copy.days = copy.days.map { day in
            var day = day
            day.state = .missing
            day.total = nil; day.input = nil; day.output = nil; day.cacheRead = nil
            return day
        }
        return copy
    }

    func testPopulatedRangeIsData() throws {
        let codex = try provider("codex")
        XCTAssertEqual(Presentation.historyAvailability(codex, days: Presentation.recentDays(codex, count: 30)), .data)
    }

    func testMeasuredZeroIsDataNotAnEmptyState() throws {
        var codex = withoutHistory(try provider("codex"))
        codex.days[codex.days.count - 2].state = .zero
        codex.days[codex.days.count - 2].total = 0
        XCTAssertEqual(Presentation.historyAvailability(codex, days: Presentation.recentDays(codex, count: 7)), .data)
        XCTAssertEqual(Presentation.historySidebarCaption(codex), "0 tokens")
    }

    func testEarlierHistoryOutsideTheRangeIsNoneInRange() throws {
        var codex = withoutHistory(try provider("codex"))
        codex.days[0].state = .measured
        codex.days[0].total = 1_000
        XCTAssertEqual(Presentation.historyAvailability(codex, days: Presentation.recentDays(codex, count: 7)), .noneInRange)
        // A provider that is no longer detected keeps its history.
        codex.setup = .notDetected
        XCTAssertEqual(Presentation.historyAvailability(codex, days: Presentation.recentDays(codex, count: 7)), .noneInRange)
        XCTAssertEqual(Presentation.historyAvailability(codex, days: Presentation.recentDays(codex, count: 90)), .data)
    }

    func testStatesWithoutAnyHistoryFollowDetectionAndConfiguration() throws {
        let gemini = try provider("gemini_cli")
        XCTAssertEqual(gemini.setup, .notDetected)
        XCTAssertEqual(Presentation.historyAvailability(gemini, days: Presentation.recentDays(gemini, count: 90)), .notDetected)
        XCTAssertEqual(Presentation.historySidebarCaption(gemini), "Not detected")

        var disabled = withoutHistory(try provider("antigravity"))
        disabled.enabled = false
        disabled.setup = .disabled
        XCTAssertEqual(Presentation.historyAvailability(disabled, days: disabled.days), .trackingDisabled)
        XCTAssertEqual(Presentation.historySidebarCaption(disabled), "Tracking off")

        // Turned off wins over "not detected": enabling is the useful action.
        disabled.setup = .notDetected
        XCTAssertEqual(Presentation.historyAvailability(disabled, days: disabled.days), .trackingDisabled)

        var empty = withoutHistory(try provider("antigravity"))
        XCTAssertEqual(empty.setup, .ready)
        XCTAssertEqual(Presentation.historyAvailability(empty, days: empty.days), .noUsageRecorded)
        XCTAssertEqual(Presentation.historySidebarCaption(empty), "No usage in 30 days")
        empty.setup = .waiting
        XCTAssertEqual(Presentation.historyAvailability(empty, days: empty.days), .noUsageRecorded)
        empty.setup = .notConfigured
        XCTAssertEqual(Presentation.historyAvailability(empty, days: empty.days), .notConfigured)
    }

    func testQuotaModeIsSupportedWheneverAWindowExistsEvenWhenStale() throws {
        let grok = try provider("grok")
        XCTAssertEqual(grok.quota.status, .stale)
        XCTAssertTrue(Presentation.quotaSupport(grok).supported)
        // Claude's plan limits come from its /usage cache (session, weekly, per model).
        XCTAssertTrue(Presentation.quotaSupport(try provider("claude")).supported)
        let gemini = try provider("gemini_cli")
        let support = Presentation.quotaSupport(gemini)
        XCTAssertFalse(support.supported)
        XCTAssertNotNil(support.reason)
    }
}
