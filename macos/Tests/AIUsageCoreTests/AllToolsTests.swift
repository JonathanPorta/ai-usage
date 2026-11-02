import Foundation
import XCTest
@testable import AIUsageCore

/// Phase 4's All tools (tasks/phase-4/prd-all-view.md): figures by rules F1–F4 (AC-5), the
/// page (AC-6), the chart's bars (AC-7), series and overflow (AC-8) and readouts (AC-9).
/// Every report comes from the synthetic fixtures; the rule cases are hand-built days.
final class AllToolsTests: XCTestCase {
    private let today = "2026-09-29"

    private func day(_ date: String, _ state: GroupDay.State, _ total: Double?, events: Int = 0,
                     parts: [GroupDay.Part] = []) -> GroupDay {
        GroupDay(date: date, state: state, total: total, incompleteEvents: events, parts: parts)
    }

    private func part(_ id: String, _ state: Day.State, _ total: Double?, asOf: Date? = nil,
                      events: Int = 0) -> GroupDay.Part {
        GroupDay.Part(id: id, state: state, total: total, asOf: asOf, incompleteEvents: events)
    }

    private func range(_ days: [GroupDay]) -> AllToolsFigure {
        AllToolsRules.rangeFigure(days.map(AllToolsReading.init), today: today)
    }

    // MARK: AC-5 Figures (F1–F4)

    func testARangeWithAFullyMissingDayReadsAtLeast() {
        let figure = range([day("2026-09-27", .measured, 100), day("2026-09-28", .missing, nil),
                            day(today, .partial, 40)])
        XCTAssertEqual(figure, AllToolsFigure(value: 140, qualified: true))
        XCTAssertEqual(figure.text, "at least 140")
    }

    func testARangeWithAPastPartialDayReadsAtLeast() {
        XCTAssertTrue(range([day("2026-09-28", .partial, 100), day(today, .partial, 40)]).qualified)
    }

    func testARangeWithAnIncompleteDayOrIncompleteEventsReadsAtLeast() {
        XCTAssertTrue(range([day("2026-09-28", .incomplete, 100), day(today, .partial, 40)]).qualified)
        XCTAssertTrue(range([day("2026-09-28", .measured, 100, events: 2), day(today, .partial, 40)]).qualified)
    }

    func testARangeWhoseOnlyPartialDayIsTodayIsUnqualified() {
        let figure = range([day("2026-09-26", .notCollected, nil), day("2026-09-27", .zero, 0),
                            day("2026-09-28", .measured, 100), day(today, .partial, 40)])
        XCTAssertEqual(figure, AllToolsFigure(value: 140, qualified: false))
        XCTAssertEqual(figure.text, "140")
    }

    func testAnEarlierIncompleteDayLeavesTodayUnqualified() {
        let days = [day("2026-09-28", .incomplete, 100), day(today, .partial, 40)]
        XCTAssertTrue(range(days).qualified)
        XCTAssertEqual(AllToolsRules.todayFigure(AllToolsReading(days[1])), AllToolsFigure(value: 40, qualified: false))
    }

    func testAZeroDayWithIncompleteTurnsIsAtLeastZero() {
        let lowZero = day("2026-09-28", .zero, 0, events: 2)
        XCTAssertEqual(range([lowZero, day(today, .partial, 0)]), AllToolsFigure(value: 0, qualified: true))
        XCTAssertEqual(AllToolsRules.todayFigure(AllToolsReading(day(today, .zero, 0, events: 2))).text, "at least 0")
        // A complete zero stays an unqualified, known 0.
        XCTAssertEqual(range([day("2026-09-28", .zero, 0), day(today, .partial, 0)]), AllToolsFigure(value: 0, qualified: false))
    }

    func testTodayIsQualifiedOnlyByItsOwnDay() {
        XCTAssertTrue(AllToolsRules.todayFigure(AllToolsReading(day(today, .incomplete, 40))).qualified)
        XCTAssertTrue(AllToolsRules.todayFigure(AllToolsReading(day(today, .partial, 40, events: 1))).qualified)
        XCTAssertEqual(AllToolsRules.todayFigure(AllToolsReading(day(today, .missing, nil))).text, "—")
        XCTAssertEqual(AllToolsRules.todayFigure(nil).text, "—")
    }

    func testNoKnownReadingIsUnknownButKnownZerosAreZero() {
        let unknown = range([day("2026-09-28", .missing, nil), day(today, .notCollected, nil)])
        XCTAssertNil(unknown.value)
        XCTAssertEqual(unknown.text, "—")
        let zeros = range([day("2026-09-28", .zero, 0), day(today, .partial, 0)])
        XCTAssertEqual(zeros.value, 0)
        XCTAssertEqual(zeros.text, "0")
    }

    func testAccountsKeepTheirOwnTimesAndNoCombinedTimeAppears() {
        let read = Date(timeIntervalSince1970: 1_790_000_000)
        let names = ["claude": "Claude Code", "claude-az": "Claude (work)"]
        let combined = day(today, .partial, 150, parts: [part("claude", .partial, 100, asOf: read),
                                                         part("claude-az", .partial, 50)])
        let text = AllToolsText.readout(combined, names: names, today: today)
        XCTAssertEqual(text, "Today so far: 150 tokens: Claude Code 100 as of \(Format.clock(read)), "
                       + "Claude (work) 50 time unknown")
        let past = day("2026-09-28", .partial, 150, parts: [part("claude", .partial, 100, asOf: read),
                                                            part("claude-az", .measured, 50)])
        XCTAssertEqual(AllToolsText.readout(past, names: names, today: today),
                       "\(Format.longDay("2026-09-28")): at least 150 tokens, day incomplete: "
                       + "Claude Code 100 read until \(Format.clock(read)), Claude (work) 50")
    }

    func testEachBreakdownRowUsesItsOwnAccountsDays() throws {
        let report = try Report.scenarioFixture("all-tools")
        let page = try XCTUnwrap(AllToolsPage(report, range: 30))
        let claude = try XCTUnwrap(page.sections.first { $0.id == "claude" })
        let rows = Dictionary(uniqueKeysWithValues: claude.rows.map { ($0.id, $0) })
        // The section has the incomplete day; Claude Code's own days are complete since
        // its collection began.
        XCTAssertTrue(claude.range.qualified)
        XCTAssertEqual(rows["claude"]?.range.qualified, false)
        XCTAssertEqual(rows["claude"]?.status, "collected from 9 Sep")
        let work = try XCTUnwrap(rows["claude-az"])
        XCTAssertTrue(work.range.qualified)
        XCTAssertEqual(work.status, "1 day missing · collected from 22 Sep")
        let own = try XCTUnwrap(report.provider("claude-az")).days.suffix(30).compactMap { $0.hasValue ? $0.total : nil }
        XCTAssertEqual(work.range.value, own.reduce(0, +))
        XCTAssertEqual(work.today, AllToolsFigure(value: 14_500, qualified: false))
    }

    // MARK: AC-4 Compatibility

    func testAReportWithoutGroupsHidesAllTools() throws {
        var report = try Report.twoAccountFixture()
        XCTAssertNotNil(report.groups)
        let encoded = try JSONEncoder.report.encode(report)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        object.removeValue(forKey: "groups")
        report = try Report.decode(JSONSerialization.data(withJSONObject: object))
        XCTAssertNil(report.groups)
        XCTAssertNil(AllToolsPage(report, range: 30))
        XCTAssertEqual(HistorySelection.allTools.resolved(in: report), .provider("codex"))
        XCTAssertEqual(HistorySelection.provider("claude").resolved(in: report), .provider("claude"))
    }

    // MARK: AC-6 The page

    func testThePageHasASectionPerToolWithAnAccountTrackedNow() throws {
        let report = try Report.scenarioFixture("all-tools")
        let page = try XCTUnwrap(AllToolsPage(report, range: 30))
        XCTAssertEqual(page.sections.map(\.id), ["codex", "claude", "antigravity", "grok", "gemini_cli"])
        let claude = try XCTUnwrap(page.sections.first { $0.id == "claude" })
        XCTAssertEqual(claude.title, "Claude Code · 2 accounts tracked now")
        XCTAssertEqual(claude.rows.map(\.name), ["Claude Code", "Claude (work)"])
        XCTAssertEqual(claude.excluded, "Not included: Claude (old), tracking off")
        XCTAssertEqual(page.sections.first { $0.id == "codex" }?.title, "Codex · 1 account tracked now")
        XCTAssertTrue(AllToolsPage.about.contains("cover the accounts tracked now"))
        XCTAssertNil(page.notIncluded)
        let gemini = try XCTUnwrap(page.sections.first { $0.id == "gemini_cli" })
        XCTAssertFalse(gemini.hasReadings)
        XCTAssertEqual(gemini.rangeCaption, "No readings in 30 days")
        XCTAssertEqual(gemini.range.text, "—")
    }

    func testAToolWithEveryAccountOffIsOnlyListedAsNotIncluded() throws {
        var report = try Report.scenarioFixture("all-tools")
        var groups = try XCTUnwrap(report.groups)
        let index = try XCTUnwrap(groups.firstIndex { $0.type == "antigravity" })
        groups[index].excluded = [ToolGroup.Excluded(id: "antigravity", reason: "disabled")]
        groups[index].members = []
        report.groups = groups
        let page = try XCTUnwrap(AllToolsPage(report, range: 7))
        XCTAssertFalse(page.sections.contains { $0.id == "antigravity" })
        XCTAssertEqual(page.notIncluded, "Not included: Antigravity, tracking off")
    }

    func testTheRangeSelectsTheLastDays() throws {
        let report = try Report.twoAccountFixture()
        for count in [7, 30, 90] {
            let page = try XCTUnwrap(AllToolsPage(report, range: count))
            XCTAssertEqual(page.sections.first?.bars.count, count)
            XCTAssertEqual(page.sections.first?.bars.last?.date, report.today)
        }
    }

    // MARK: AC-7 Chart marks

    func testAnIncompleteBarIsAsTallAsItsKnownTotalAndItsMarkerAddsNothing() throws {
        let report = try Report.scenarioFixture("all-tools")
        let claude = try XCTUnwrap(AllToolsPage(report, range: 30)?.sections.first { $0.id == "claude" })
        let scale = AllToolsBar.scale(claude.bars, plotHeight: 160)
        let peak = try XCTUnwrap(claude.bars.compactMap(\.total).max())
        XCTAssertEqual(scale, 160 / (peak * 1.08), accuracy: 1e-9)
        for bar in claude.bars {
            XCTAssertEqual(bar.heights(scale: scale).reduce(0, +), (bar.total ?? 0) * scale, accuracy: 1e-9, bar.date)
            XCTAssertEqual(bar.marker, bar.state == .incomplete, bar.date)
        }
        let incomplete = try XCTUnwrap(claude.bars.first { $0.state == .incomplete })
        XCTAssertEqual(incomplete.missing, ["Claude (work)"])
        XCTAssertEqual(incomplete.segments.map(\.series), [0])
        XCTAssertEqual(claude.bars.filter(\.marker).count, 1)
    }

    func testTheScaleIgnoresMissingAccountsAndEveryStateKeepsItsMark() {
        let names = ["a": "A", "b": "B"]
        let series = ["a": 0, "b": 1]
        let bars = [
            AllToolsBar(day("d1", .incomplete, 50, parts: [part("a", .measured, 50), part("b", .missing, nil)]),
                        names: names, seriesOf: series, today: today),
            AllToolsBar(day("d2", .missing, nil, parts: [part("a", .missing, nil), part("b", .missing, nil)]),
                        names: names, seriesOf: series, today: today),
            AllToolsBar(day("d3", .incomplete, 0, parts: [part("a", .zero, 0), part("b", .missing, nil)]),
                        names: names, seriesOf: series, today: today),
            AllToolsBar(day("d4", .notCollected, nil, parts: [part("a", .notCollected, nil), part("b", .notCollected, nil)]),
                        names: names, seriesOf: series, today: today),
        ]
        XCTAssertEqual(AllToolsBar.scale(bars, plotHeight: 108), 108 / (50 * 1.08), accuracy: 1e-9)
        XCTAssertEqual(bars.map(\.marker), [true, false, true, false])
        XCTAssertEqual(bars.map { $0.segments.count }, [1, 0, 0, 0])
        XCTAssertEqual(AllToolsBar.scale([bars[1], bars[3]], plotHeight: 108), 0)
    }

    // MARK: AC-8 Series and overflow

    func testUpToFourAccountsAreTheirOwnSeries() {
        for count in [1, 3, 4] {
            let members = (0..<count).map { "a\($0)" }
            let series = AllToolsSeries.assign(members, names: [:])
            XCTAssertEqual(series.map(\.index), Array(0..<count))
            XCTAssertEqual(series.map(\.members), members.map { [$0] })
        }
    }

    func testFromFiveOnTheRestAreOtherAccounts() {
        for count in [5, 7] {
            let members = (0..<count).map { "a\($0)" }
            let names = Dictionary(uniqueKeysWithValues: members.map { ($0, $0.uppercased()) })
            let series = AllToolsSeries.assign(members, names: names)
            XCTAssertEqual(series.map(\.index), [0, 1, 2, 3])
            XCTAssertEqual(series.prefix(3).map(\.label), ["A0", "A1", "A2"])
            XCTAssertEqual(series[3].label, "Other accounts (\(count - 3))")
            XCTAssertEqual(series[3].members, Array(members.dropFirst(3)))
        }
    }

    func testTheOverflowFixtureKeepsEveryAccountNamed() throws {
        let report = try Report.scenarioFixture("overflow")
        let claude = try XCTUnwrap(AllToolsPage(report, range: 30)?.sections.first { $0.id == "claude" })
        let lab = "Claude (research lab, long-running evaluations)"
        let oss = "Claude (open-source maintainers, shared team account)"
        XCTAssertEqual(claude.series.map(\.label), ["Claude Code", "Claude (work)", lab, "Other accounts (2)"])
        XCTAssertEqual(claude.rows.map(\.name), ["Claude Code", "Claude (work)", lab, oss, "Claude (ci)"])
        XCTAssertEqual(claude.rows.map(\.id), ["claude", "claude-az", "claude-lab", "claude-oss", "claude-ci"])
        XCTAssertEqual(claude.rows.map(\.series), [0, 1, 2, 3, 3])
        let group = try XCTUnwrap(report.groups?.first { $0.type == "claude" })
        for (bar, day) in zip(claude.bars, group.days.suffix(30)) {
            let others = day.parts.filter { ["claude-oss", "claude-ci"].contains($0.id) }.compactMap(\.total).reduce(0, +)
            XCTAssertEqual(bar.segments.first { $0.series == 3 }?.value ?? 0, others, bar.date)
            for name in ["Claude Code", "Claude (work)", lab, oss, "Claude (ci)"]
                where day.state != .notCollected {
                XCTAssertTrue(bar.readout.contains(name), "\(bar.date): \(bar.readout)")
            }
        }
        XCTAssertTrue(claude.bars.contains { $0.segments.contains { $0.series == 3 } })
    }

    func testAMissingAccountInsideOtherAccountsGetsTheMarker() {
        let members = ["a", "b", "c", "d", "e"]
        let names = Dictionary(uniqueKeysWithValues: members.map { ($0, $0.uppercased()) })
        let series = Dictionary(uniqueKeysWithValues: AllToolsSeries.assign(members, names: names)
            .flatMap { s in s.members.map { ($0, s.index) } })
        let bar = AllToolsBar(day("d1", .incomplete, 60, parts: [
            part("a", .measured, 10), part("b", .measured, 10), part("c", .measured, 10),
            part("d", .measured, 30), part("e", .missing, nil),
        ]), names: names, seriesOf: series, today: today)
        XCTAssertTrue(bar.marker)
        XCTAssertEqual(bar.missing, ["E"])
        XCTAssertEqual(bar.segments.last, AllToolsSegment(series: 3, value: 30))
    }

    // MARK: AC-9 Readouts

    func testTheIncompleteReadoutNamesEveryAccount() throws {
        let report = try Report.scenarioFixture("all-tools")
        let claude = try XCTUnwrap(AllToolsPage(report, range: 30)?.sections.first { $0.id == "claude" })
        let incomplete = try XCTUnwrap(claude.bars.first { $0.state == .incomplete })
        XCTAssertEqual(incomplete.readout, "\(Format.longDay(incomplete.date)): at least 569K tokens: "
                       + "Claude Code 569K, Claude (work) missing reading")
    }

    func testReadoutsForEveryState() {
        let names = ["a": "A", "b": "B"]
        func text(_ day: GroupDay) -> String { AllToolsText.readout(day, names: names, today: today) }
        let date = Format.longDay("2026-09-28")
        XCTAssertEqual(text(day("2026-09-28", .notCollected, nil)), "\(date): not collected")
        XCTAssertEqual(text(day("2026-09-28", .missing, nil, parts: [part("a", .missing, nil), part("b", .notCollected, nil)])),
                       "\(date): no data — missing reading, not zero: A missing reading, B not collected")
        XCTAssertEqual(text(day("2026-09-28", .zero, 0, parts: [part("a", .zero, 0), part("b", .zero, 0)])),
                       "\(date): 0 tokens — measured, no usage: A 0, B 0")
        XCTAssertEqual(text(day("2026-09-28", .measured, 30, events: 3, parts: [part("a", .measured, 20, events: 3),
                                                                             part("b", .measured, 10)])),
                       "\(date): at least 30 tokens: A 20 (3 turns reported incomplete usage), B 10")
        // A zero with incomplete turns may be low: "at least 0", never "measured, no usage".
        let lowZero = day("2026-09-28", .zero, 0, events: 2, parts: [part("a", .zero, 0, events: 2), part("b", .zero, 0)])
        XCTAssertEqual(text(lowZero), "\(date): at least 0 tokens — 2 turns reported incomplete usage: "
                       + "A 0 (2 turns reported incomplete usage), B 0")
        XCTAssertEqual(text(day("2026-09-28", .zero, 0, events: 1, parts: [part("a", .zero, 0, events: 1)])),
                       "\(date): at least 0 tokens — 1 turn reported incomplete usage: A 0 (1 turn reported incomplete usage)")
        // One account: its part is left out unless it adds a time or incomplete turns.
        XCTAssertEqual(text(day("2026-09-28", .measured, 20, parts: [part("a", .measured, 20)])), "\(date): 20 tokens")
        XCTAssertEqual(text(day(today, .partial, 20, parts: [part("a", .partial, 20)])),
                       "Today so far: 20 tokens: A 20 time unknown")
    }
}
