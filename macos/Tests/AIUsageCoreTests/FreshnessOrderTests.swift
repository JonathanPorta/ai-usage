import Foundation
import XCTest
@testable import AIUsageCore

final class ClockBox: @unchecked Sendable {
    var now: Date
    init(_ now: Date) { self.now = now }
}

/// Service probe whose results (and completion order) the test controls.
@MainActor
final class ScriptedService: ServiceControlling, @unchecked Sendable {
    var results: [Service.State] = []
    var holdNext = false
    var held: [CheckedContinuation<Void, Never>] = []

    nonisolated func status() async throws -> ServiceStatus {
        let (state, hold) = await MainActor.run { () -> (Service.State, Bool) in
            let hold = self.holdNext
            self.holdNext = false
            return (self.results.removeFirst(), hold)
        }
        if hold {
            await withCheckedContinuation { continuation in
                Task { @MainActor in self.held.append(continuation) }
            }
        }
        return ServiceStatus(installed: true, state: state, pid: state == .running ? 1 : nil,
                             disabled: state == .stopped, detail: "")
    }
    nonisolated func start() async throws {}
    nonisolated func stop() async throws {}
}

func withService(_ report: Report, _ state: Service.State, generatedAt: Date) -> Report {
    var copy = report
    copy.service.state = state
    copy.generatedAt = generatedAt
    return copy
}

@MainActor
final class ServiceFreshnessOrderTests: XCTestCase {
    func testNewerReportSupersedesAnOlderRunningProbe() async throws {
        let base = try fixture()
        let clock = ClockBox(base.generatedAt.addingTimeInterval(60))
        let later = withService(base, .stopped, generatedAt: base.generatedAt.addingTimeInterval(120))
        let reporter = SpyReporter([.success(withService(base, .running, generatedAt: base.generatedAt)), .success(later)])
        let service = ScriptedService()
        service.results = [.running]
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector(),
                             service: service, clock: { clock.now })
        await store.refresh()
        await store.probeService()                     // observed running at +60
        XCTAssertEqual(store.report?.service.state, .running)
        clock.now = base.generatedAt.addingTimeInterval(180)
        await store.refresh()                          // report generated at +120 says stopped
        XCTAssertEqual(store.report?.service.state, .stopped, "newer report evidence wins over an older probe")
    }

    func testNewerProbeSupersedesAnOlderStoppedReport() async throws {
        let base = try fixture()
        let clock = ClockBox(base.generatedAt.addingTimeInterval(60))
        let reporter = SpyReporter([.success(withService(base, .stopped, generatedAt: base.generatedAt))])
        let service = ScriptedService()
        service.results = [.running]
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector(),
                             service: service, clock: { clock.now })
        await store.refresh()
        XCTAssertEqual(store.report?.service.state, .stopped)
        await store.probeService()                     // probe at +60 says running
        XCTAssertEqual(store.report?.service.state, .running)
    }

    func testDelayedOlderProbeCannotOverwriteANewerProbe() async throws {
        let base = try fixture()
        let clock = ClockBox(base.generatedAt.addingTimeInterval(60))
        let service = ScriptedService()
        service.results = [.running, .stopped]
        service.holdNext = true                        // the first probe is slow
        let store = AppStore(environment: environment(), reporter: SpyReporter([.success(base)]),
                             collector: SpyCollector(), service: service, clock: { clock.now })
        await store.refresh()
        let slow = Task { await store.probeService() }  // starts at +60, will say running
        for _ in 0..<200 where service.held.isEmpty { try await Task.sleep(nanoseconds: 2_000_000) }
        clock.now = base.generatedAt.addingTimeInterval(120)
        await store.probeService()                     // starts at +120, says stopped, finishes first
        XCTAssertEqual(store.report?.service.state, .stopped)
        service.held.forEach { $0.resume() }
        await slow.value
        XCTAssertEqual(store.report?.service.state, .stopped, "an older probe finishing late must not win")
        XCTAssertEqual(store.liveService?.observedAt, base.generatedAt.addingTimeInterval(120))
    }
}

@MainActor
final class CalendarRolloverTests: XCTestCase {
    /// Fixture: generated Tue 29 Sep 10:40 America/Denver. Next morning, 09:00.
    func nextMorning(_ report: Report) -> Date {
        report.generatedAt.addingTimeInterval(22 * 3600 + 20 * 60)
    }

    func testStoppedCollectorAcrossMidnightNeverLabelsYesterdayAsToday() throws {
        let report = try fixture()
        let rolled = report.evaluated(at: nextMorning(report))
        XCTAssertEqual(report.today, "2026-09-29")
        XCTAssertEqual(rolled.today, "2026-09-30")
        for provider in rolled.providers {
            let original = try XCTUnwrap(report.provider(provider.id))
            XCTAssertNil(provider.today, "\(provider.id): yesterday's totals are not today's")
            XCTAssertTrue(provider.modelsToday.isEmpty, "\(provider.id): model totals belong to yesterday")
            XCTAssertEqual(provider.days.count, original.days.count, "window length is kept")
            XCTAssertEqual(provider.days.last?.date, "2026-09-30", "the window ends on the new day")
            XCTAssertEqual(provider.days.last?.state, original.days.last?.state == .notCollected ? .missing : .missing)
            if let yesterday = provider.days.first(where: { $0.date == "2026-09-29" }), original.today != nil {
                XCTAssertEqual(yesterday.state, .partial)
                XCTAssertEqual(yesterday.total, original.today?.total, "yesterday's measurement itself is unchanged")
                XCTAssertEqual(yesterday.asOf, original.today?.asOf)
                let text = Presentation.readout(provider, day: yesterday, asOf: nil, today: rolled.today)
                XCTAssertFalse(text.hasPrefix("Today"), text)
                XCTAssertTrue(text.contains("day incomplete"), text)
            }
        }
        // The same day stays untouched.
        XCTAssertEqual(report.evaluated(at: report.generatedAt.addingTimeInterval(3600)).today, "2026-09-29")
    }

    func testRefreshFailureAfterMidnightKeepsTheHonestRolledView() async throws {
        let report = try fixture()
        let clock = ClockBox(nextMorning(report))
        let store = AppStore(environment: environment(), reporter: SpyReporter([.failure(Boom())]),
                             collector: SpyCollector(), clock: { clock.now })
        store.apply(report, cached: true)
        await store.refreshIfSourcesChanged()
        XCTAssertEqual(store.refreshError, "report exploded")
        XCTAssertEqual(store.report?.today, "2026-09-30")
        XCTAssertNil(store.report?.provider("claude")?.today)
    }

    func testMidnightTriggersOneRereadWhileSameDayReopensStayScanFree() async throws {
        let report = try fixture()
        let clock = ClockBox(report.generatedAt.addingTimeInterval(60))
        let reporter = SpyReporter([.success(report)])
        let marker = FingerprintBox()
        let store = AppStore(environment: environment(), reporter: reporter, collector: SpyCollector(),
                             clock: { clock.now }, fingerprint: { _ in marker.value })
        await store.refresh()
        clock.now = report.generatedAt.addingTimeInterval(5 * 3600)   // 15:40, same day, files unchanged
        for _ in 0..<3 { await store.refreshIfSourcesChanged() }
        XCTAssertEqual(reporter.calls, 1, "no scan for same-day reopens with unchanged files")
        clock.now = nextMorning(report)                                // files still unchanged
        await store.refreshIfSourcesChanged()
        XCTAssertEqual(reporter.calls, 2, "a new calendar day re-reads even with unchanged files")
        // The scripted reporter still returns the 29th; that must not cause a rescan loop.
        for _ in 0..<3 { await store.refreshIfSourcesChanged() }
        XCTAssertEqual(reporter.calls, 2, "at most one re-read per calendar day")
    }
}
