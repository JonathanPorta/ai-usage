import Foundation
import XCTest
@testable import AIUsageCore

final class MonitoringPageTests: XCTestCase {
    func testEveryStatusHasSymbolAndNonHealthyHasLabel() {
        let all: [Source.Status] = [.ok, .failed, .stale, .auth, .off, .waiting, .notDetected]
        let badges = all.map(MonitoringPage.badge)
        XCTAssertEqual(Set(badges.map(\.symbol)).count, all.count, "each status has its own symbol")
        for badge in badges where badge.kind != .healthy { XCTAssertFalse(badge.label.isEmpty) }
        XCTAssertEqual(MonitoringPage.badge(.ok).label, "")
        XCTAssertEqual(MonitoringPage.badge(.ok).accessibilityLabel, "Healthy")
        XCTAssertEqual(MonitoringPage.badge(.auth).kind, .signIn)
        XCTAssertEqual(MonitoringPage.badge(.notDetected).label, "Missing")
        XCTAssertTrue(MonitoringPage.badge(.stale).isProblem)
        XCTAssertFalse(MonitoringPage.badge(.off).isProblem)
    }

    func testGroupsSplitInactiveProvidersOut() throws {
        var report = try fixture()
        report.set("antigravity") { $0.tracking = .off; $0.trackingEffective = .inactive; $0.enabled = false }
        report.set("gemini_cli") { $0.tracking = .auto; $0.trackingEffective = .pending }
        let groups = MonitoringPage.sourceGroups(report)
        XCTAssertEqual(groups.inactive.map(\.id), ["antigravity", "gemini_cli"], "awaiting detection is not collected yet")
        XCTAssertEqual(groups.active.count + groups.inactive.count, report.providers.count)
        let grok = try XCTUnwrap(groups.active.first { $0.id == "grok" })
        XCTAssertEqual(grok.problemCount, 1)
    }

    func testLegacyDisabledProviderIsInactive() throws {
        var report = try legacyBase()
        report.set("codex") { $0.enabled = false }
        XCTAssertEqual(MonitoringPage.sourceGroups(report).inactive.map(\.id), ["codex"])
    }

    func testSourceDetailKeepsReadAndMeasuredDistinct() throws {
        var source = try XCTUnwrap(fixture().providers.first?.sources.first)
        source.lastReadAt = Date(timeIntervalSince1970: 1_000_000)
        source.measuredAt = Date(timeIntervalSince1970: 900_000)
        source.lastAttemptAt = source.lastReadAt
        source.error = "timed out"
        let detail = MonitoringPage.sourceDetail(source)
        XCTAssertTrue(detail.hasPrefix("Read "))
        XCTAssertTrue(detail.contains("measured "))
        XCTAssertFalse(detail.contains("last tried"))
        XCTAssertTrue(detail.hasSuffix("timed out"))
        source.lastReadAt = nil
        XCTAssertTrue(MonitoringPage.sourceDetail(source).hasPrefix("Never read"))
    }

    func testLastSuccessShownOnlyWhenItDiffersMeaningfully() {
        let now = Date(timeIntervalSince1970: 2_000_000)
        var collection = CollectionState(lastAttemptAt: now, lastAttemptResult: .success, lastSuccessAt: now,
                                         failures: [], summary: nil)
        XCTAssertNil(MonitoringPage.lastSuccessWorthShowing(collection))
        collection.lastSuccessAt = now.addingTimeInterval(-120)
        XCTAssertNil(MonitoringPage.lastSuccessWorthShowing(collection))
        collection.lastSuccessAt = now.addingTimeInterval(-3600)
        XCTAssertEqual(MonitoringPage.lastSuccessWorthShowing(collection), now.addingTimeInterval(-3600))
        collection.lastSuccessAt = now
        collection.lastAttemptResult = .partial
        XCTAssertNil(MonitoringPage.lastSuccessWorthShowing(collection), "same time as the attempt")
        collection.lastSuccessAt = now.addingTimeInterval(-7200)
        XCTAssertEqual(MonitoringPage.lastSuccessWorthShowing(collection), now.addingTimeInterval(-7200))
        collection.lastSuccessAt = nil
        XCTAssertNil(MonitoringPage.lastSuccessWorthShowing(collection))
    }

    func testServiceTechnicalDetailsAreSeparateAndNoQuitPromise() {
        let running = MonitoringPage.service(Service(state: .running, pid: 4242, disabled: false, detail: "launchctl: running"))
        XCTAssertEqual(running.title, "Running")
        XCTAssertTrue(running.running)
        XCTAssertTrue(running.technical.contains("PID 4242"))
        XCTAssertFalse(running.title.contains("quits"))
        let stopped = MonitoringPage.service(Service(state: .stopped, pid: nil, disabled: true, detail: ""))
        XCTAssertFalse(stopped.running)
        XCTAssertEqual(stopped.title, "Stopped · stays stopped at login")
    }

    func testExclusionsListSkippedAndInactiveOnce() throws {
        var report = try fixture()
        XCTAssertNil(MonitoringPage.exclusions(report))
        report.set("antigravity") { $0.tracking = .off; $0.trackingEffective = .inactive }
        report.collection.summary?.skipped = ["Antigravity"]
        let name = try XCTUnwrap(report.provider("antigravity")?.name)
        report.collection.summary?.skipped = [name]
        XCTAssertEqual(MonitoringPage.exclusions(report), "Not checked: \(name) · their saved values are kept")
    }

    func testResultCountsTheSameProvidersThePageListsAsActive() throws {
        var report = try fixture()
        report.set("antigravity") { $0.tracking = .off; $0.trackingEffective = .inactive }
        report.set("gemini_cli") { $0.trackingEffective = .pending }
        let active = MonitoringPage.sourceGroups(report).active.count
        XCTAssertEqual(active, 3)
        XCTAssertEqual(HeaderFacts.result(report).text, "\(active) providers checked")
    }

    func testUpdatedIsImpossibleWithAnUnhealthyActiveSource() throws {
        var report = try fixture()
        let expected = report.collection.summary?.providersExpected
        report.collection.summary?.providersFresh = expected
        for status: Source.Status in [.stale, .failed, .auth, .notDetected, .waiting] {
            for i in report.providers.indices {
                for j in report.providers[i].sources.indices { report.providers[i].sources[j].status = .ok }
            }
            XCTAssertEqual(HeaderFacts.result(report).kind, .success)
            report.set("codex") { $0.sources[0].status = status }
            let result = HeaderFacts.result(report)
            XCTAssertNotEqual(result.kind, .success, "\(status)")
            XCTAssertFalse(result.text.contains("updated"), "\(status)")
        }
    }
}
