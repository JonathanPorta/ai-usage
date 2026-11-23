import XCTest
@testable import AIUsageCore

/// Provider detail: tracking toggle mapping, empty-state selection and the
/// context History opens with.
final class DetailPresentationTests: XCTestCase {
    private func provider(_ id: String) throws -> Provider { try XCTUnwrap(try fixture().provider(id)) }

    private func clearing(_ provider: Provider, keepingFirst keep: Int = 0) -> Provider {
        var copy = provider
        for i in copy.days.indices where i >= keep && copy.days[i].state != .notCollected {
            copy.days[i].state = .missing
            copy.days[i].total = nil; copy.days[i].input = nil; copy.days[i].output = nil; copy.days[i].cacheRead = nil
        }
        return copy
    }

    // MARK: Toggle

    func testToggleMapsOffToOffAndOnToOn() throws {
        let codex = try provider("codex")
        XCTAssertEqual(DetailPresentation.trackingChange(codex, on: false, supportsPolicy: true), .providerTracking("codex", .off))
        XCTAssertEqual(DetailPresentation.trackingChange(codex, on: true, supportsPolicy: true), .providerTracking("codex", .on))
    }

    func testToggleWithoutTrackingPolicyNeverReplacesASavedAuto() throws {
        var codex = try provider("codex")
        XCTAssertEqual(DetailPresentation.trackingChange(codex, on: false, supportsPolicy: false), .providerTracking("codex", .off))
        XCTAssertEqual(DetailPresentation.trackingChange(codex, on: true, supportsPolicy: false), .providerTracking("codex", .on))
        codex.tracking = .auto
        XCTAssertEqual(DetailPresentation.trackingChange(codex, on: true, supportsPolicy: false), .providerTracking("codex", .auto),
                       "an older collector collects Auto: On keeps the saved Auto")
        XCTAssertEqual(DetailPresentation.trackingChange(codex, on: false, supportsPolicy: false), .providerTracking("codex", .off))
        XCTAssertEqual(DetailPresentation.trackingChange(codex, on: true, supportsPolicy: true), .providerTracking("codex", .on),
                       "with the policy, the switch sets On explicitly")
        let account = try fixture().adding(instance: "claude-az", copying: "claude", name: "Claude (az)").provider("claude-az")!
        var auto = account
        auto.tracking = .auto
        XCTAssertEqual(DetailPresentation.trackingChange(auto, on: true, supportsPolicy: false).assignment,
                       "instances.claude-az.tracking=\"auto\"")
        var report = try fixture()
        report.collector.capabilities?.trackingPolicy = nil
        XCTAssertFalse(DetailPresentation.supportsPolicy(report))
    }

    func testAutoShowsThatAutoControlsTheState() throws {
        var codex = try provider("codex")
        codex.tracking = .auto
        codex.trackingEffective = .active
        let toggle = DetailPresentation.trackingToggle(codex, pending: nil)
        XCTAssertTrue(toggle.isOn)
        XCTAssertTrue(toggle.autoControlled)
        XCTAssertEqual(toggle.status, "Auto · tracking")
        codex.trackingEffective = .inactive
        XCTAssertFalse(DetailPresentation.trackingToggle(codex, pending: nil).isOn)
    }

    func testExplicitAndPendingStates() throws {
        var codex = try provider("codex")
        codex.tracking = .off
        XCTAssertEqual(DetailPresentation.trackingToggle(codex, pending: nil), .init(isOn: false, status: "Off", autoControlled: false))
        XCTAssertEqual(DetailPresentation.trackingToggle(codex, pending: true), .init(isOn: true, status: "Saving…", autoControlled: false))
        codex.tracking = .on
        XCTAssertTrue(DetailPresentation.trackingToggle(codex, pending: nil).isOn)
    }

    // MARK: Empty states

    func testChartWithDataHasNoEmptyState() throws {
        let codex = try provider("codex")
        XCTAssertNil(DetailPresentation.chartEmpty(codex, days: Presentation.recentDays(codex, count: 7), range: 7,
                                                   ranges: DetailPresentation.detailTokenRanges))
    }

    func testNoUsageEverRecorded() throws {
        let codex = clearing(try provider("codex"))
        let copy = try XCTUnwrap(DetailPresentation.chartEmpty(codex, days: Presentation.recentDays(codex, count: 7), range: 7,
                                                               ranges: DetailPresentation.detailTokenRanges))
        XCTAssertEqual(copy.title, "No usage recorded yet")
    }

    func testNoDataInRangeOffersAWiderRangeThenHistory() throws {
        var codex = clearing(try provider("codex"))
        codex.days[0].state = .measured
        codex.days[0].total = 1_000
        let seven = try XCTUnwrap(DetailPresentation.chartEmpty(codex, days: Presentation.recentDays(codex, count: 7), range: 7,
                                                                ranges: DetailPresentation.detailTokenRanges))
        XCTAssertEqual(seven.title, "No data in this range")
        XCTAssertEqual(seven.action, .widenRange(30))
        let thirty = DetailPresentation.chartEmpty(codex, days: Presentation.recentDays(codex, count: 30), range: 30,
                                                   ranges: DetailPresentation.detailTokenRanges)
        XCTAssertEqual(thirty?.action, .openHistoryRange(90))
        let ninety = DetailPresentation.chartEmpty(codex, days: Presentation.recentDays(codex, count: 90), range: 90,
                                                   ranges: DetailPresentation.historyTokenRanges)
        XCTAssertNil(ninety?.action)
    }

    func testTrackingOffWithoutHistoryOffersEnable() throws {
        var codex = clearing(try provider("codex"))
        codex.tracking = .off
        let copy = DetailPresentation.chartEmpty(codex, days: Presentation.recentDays(codex, count: 7), range: 7,
                                                 ranges: DetailPresentation.detailTokenRanges)
        XCTAssertEqual(copy?.action, .enableTracking)
    }

    func testTodayStates() throws {
        var codex = try provider("codex")
        if codex.today != nil { XCTAssertEqual(DetailPresentation.todayState(codex), .reading) }
        codex.today = nil
        XCTAssertEqual(DetailPresentation.todayState(codex), .noReadingToday)
        XCTAssertEqual(DetailPresentation.todayState(clearing(codex)), .noUsageEver)
    }

    func testReportedCostAndSubscriptionStaySeparate() throws {
        var codex = try provider("codex")
        codex.costs.reported = nil
        codex.costs.subscription = Costs.Subscription(monthlyUsd: 20, source: "config", enteredByUser: true)
        // A price never stands in for reported cost.
        XCTAssertEqual(DetailPresentation.reportedCostEmpty(codex)?.title, "Usage cost isn't reported by this provider")
        XCTAssertNil(DetailPresentation.reportedCostEmpty(codex)?.action)
        XCTAssertNil(DetailPresentation.subscriptionEmpty(codex))
        codex.costs.subscription = nil
        XCTAssertEqual(DetailPresentation.subscriptionEmpty(codex)?.title, "Add subscription price")
        XCTAssertEqual(DetailPresentation.subscriptionEmpty(codex)?.action, .providerSettings)
    }

    // MARK: History context

    func testHistoryContextKeepsMetricWindowAndCompatibleRange() throws {
        let report = try fixture()
        let quota = try XCTUnwrap(report.providers.first { !$0.activeWindows.isEmpty })
        let window = try XCTUnwrap(quota.activeWindows.last)
        let range = try XCTUnwrap(window.ranges.last)
        let context = DetailPresentation.historyContext(quota, metric: .quota, tokenRange: 7, windowId: window.id, quotaRange: range)
        XCTAssertEqual(context, HistoryContext(providerId: quota.id, metric: .quota, tokenRange: 7, windowId: window.id, quotaRange: range))
        let incompatible = DetailPresentation.historyContext(quota, metric: .quota, tokenRange: 14, windowId: window.id, quotaRange: "999d")
        XCTAssertEqual(incompatible.tokenRange, 30)
        XCTAssertNil(incompatible.quotaRange)
    }

    func testHistoryContextFallsBackToTokensWithoutQuota() throws {
        var codex = try provider("codex")
        codex.quota.windows = []
        let context = DetailPresentation.historyContext(codex, metric: .quota, tokenRange: 30, windowId: "x", quotaRange: "7d")
        XCTAssertEqual(context.metric, .tokens)
        XCTAssertNil(context.windowId)
        XCTAssertEqual(context.tokenRange, 30)
    }

    // MARK: Review fixes

    private func empty(_ p: Provider, range: Int = 30, ranges: [Int] = [90]) -> EmptyCopy? {
        DetailPresentation.chartEmpty(p, days: Presentation.recentDays(p, count: range), range: range, ranges: ranges)
    }

    func testNotDetectedAndNotConfiguredKeepBothActions() throws {
        var p = clearing(try provider("codex"))
        p.setup = .notDetected
        let notFound = try XCTUnwrap(empty(p))
        XCTAssertEqual(notFound.title, "Codex not detected")
        XCTAssertEqual(notFound.action, .checkAgain)
        XCTAssertEqual(notFound.secondary, .providerSettings)
        p.setup = .notConfigured
        let notSetUp = try XCTUnwrap(empty(p))
        XCTAssertEqual(notSetUp.action, .providerSettings)
        XCTAssertEqual(notSetUp.secondary, .checkAgain)
        p.setup = .ready
        XCTAssertEqual(empty(p)?.action, .checkAgain)
    }

    func testHistoryNoneInRangeOffersNinetyDays() throws {
        var p = clearing(try provider("codex"))
        p.days[0].state = .measured; p.days[0].total = 1
        XCTAssertEqual(empty(p, range: 7)?.action, .widenRange(90))
        XCTAssertNil(empty(p, range: 90)?.action)
    }

    func testTrackingOffReadsAsLastKnown() throws {
        var p = try provider("codex")
        p.tracking = .off
        XCTAssertEqual(DetailPresentation.todayState(p), .notCollecting)
        let now = try fixture().generatedAt
        for window in DetailPresentation.displayWindows(p) {
            XCTAssertEqual(window.status, .stale)
            XCTAssertNotNil(window.staleCause)
            XCTAssertFalse(Presentation.quotaCaption(window, now: now).text.hasPrefix("Resets"))
            if let reset = Presentation.quotaReset(window, now: now), reset.countdown.text.contains("esets in") {
                XCTAssertTrue(reset.countdown.text.hasPrefix("Last known"))
            }
        }
        p.tracking = .on
        XCTAssertEqual(DetailPresentation.displayWindows(p), p.activeWindows)
    }

    func testPendingDetectionIsWaitingNotOff() throws {
        var p = try provider("codex")
        p.tracking = .auto; p.trackingEffective = .pending
        let toggle = DetailPresentation.trackingToggle(p, pending: nil)
        XCTAssertTrue(toggle.waiting)
        XCTAssertEqual(toggle.status, "Auto · waiting for detection")
    }

    func testCostWordingClaimsOnlyWhatIsKnown() throws {
        var p = try provider("codex")
        p.costs.reported = nil
        p.costs.reportsCost = nil
        p.costs.reportedNote = "Codex does not report a usage cost."
        XCTAssertEqual(DetailPresentation.reportedCostEmpty(p)?.title, "Usage cost isn't reported by this provider")
        p.costs.reportedNote = "No billing snapshot has been read yet."
        XCTAssertEqual(DetailPresentation.reportedCostEmpty(p)?.title, "No cost reading yet")
        p.costs.reportedNote = nil
        XCTAssertEqual(DetailPresentation.reportedCostEmpty(p)?.title, "No cost reading yet")
        p.tracking = .off
        XCTAssertEqual(DetailPresentation.reportedCostEmpty(p)?.detail, "Tracking is off, so no cost is read.")
    }

    func testStructuredReportsCostWinsOverNoteText() throws {
        var p = try provider("codex")
        p.costs.reported = nil
        p.costs.reportedNote = "Anything at all"
        p.costs.reportsCost = false
        XCTAssertEqual(DetailPresentation.reportedCostEmpty(p)?.title, "Usage cost isn't reported by this provider")
        p.costs.reportedNote = "This provider does not report it yet"
        p.costs.reportsCost = true
        XCTAssertEqual(DetailPresentation.reportedCostEmpty(p)?.title, "No cost reading yet")
    }

    func testNotStaleNoteIsHiddenWhileQuotaShowsStale() throws {
        var p = try provider("antigravity")
        p.quota.note = "Between sessions the last reading is kept; that is normal, not stale."
        p.tracking = .auto
        for i in p.quota.windows.indices { p.quota.windows[i].status = .current }
        p.quota.status = .current
        XCTAssertEqual(DetailPresentation.quotaNote(p), p.quota.note)
        p.quota.status = .stale
        XCTAssertNil(DetailPresentation.quotaNote(p))
        p.quota.status = .current
        p.tracking = .off
        XCTAssertNil(DetailPresentation.quotaNote(p), "not collecting shows the windows as Stale")
        p.quota.note = "Some other note."
        XCTAssertEqual(DetailPresentation.quotaNote(p), "Some other note.")
    }
}
