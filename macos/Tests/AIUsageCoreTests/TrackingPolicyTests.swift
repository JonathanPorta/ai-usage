import Foundation
import XCTest
@testable import AIUsageCore

/// The fixture with every tracking-policy field cleared, so tests never depend on
/// whether the fixture files already carry the new keys.
func legacyBase() throws -> Report {
    var report = try fixture()
    for index in report.providers.indices {
        report.providers[index].tracking = nil
        report.providers[index].trackingEffective = nil
        report.providers[index].detection = nil
        report.providers[index].lastActivityAt = nil
        report.providers[index].enabled = true
    }
    report.collector.capabilities?.trackingPolicy = nil
    if report.collection.summary != nil {
        report.collection.summary?.providersExpected = nil
        report.collection.summary?.providersFresh = nil
        report.collection.summary?.providersStale = nil
        report.collection.summary?.outcomes = nil
    }
    return report
}

extension Report {
    mutating func set(_ id: String, _ body: (inout Provider) -> Void) {
        guard let index = providers.firstIndex(where: { $0.id == id }) else { return XCTFail("no provider \(id)") }
        body(&providers[index])
    }

    func adding(instance id: String, copying source: String, name: String) -> Report {
        var copy = self
        var provider = copy.provider(source)!
        provider.id = id
        provider.type = source
        provider.name = name
        copy.providers.append(provider)
        return copy
    }
}

func policy(_ mode: TrackingMode?, _ effective: TrackingEffective?, enabled: Bool = true) -> (inout Provider) -> Void {
    { $0.tracking = mode; $0.trackingEffective = effective; $0.enabled = enabled }
}

final class TrackingDecodingTests: XCTestCase {
    func testUnknownValuesDegrade() throws {
        let report = try roundTrip { json in
            var providers = json["providers"] as! [[String: Any]]
            providers[0]["tracking"] = "sometimes"
            providers[0]["tracking_effective"] = "dormant"
            providers[0]["enabled"] = false
            providers[1]["detection"] = ["installed": false, "requirement": "service", "saved_data": true]
            json["providers"] = providers
            var collection = json["collection"] as! [String: Any]
            var summary = collection["summary"] as! [String: Any]
            summary["providers_expected"] = 1
            summary["providers_fresh"] = 0
            summary["providers_stale"] = 0
            summary["outcomes"] = [["id": "codex", "outcome": "superb"]]
            collection["summary"] = summary
            json["collection"] = collection
        }
        XCTAssertEqual(report.providers[0].tracking, .unknown)
        XCTAssertEqual(report.providers[0].trackingMode, .off, "falls back to enabled")
        XCTAssertEqual(report.providers[0].effectiveTracking, .inactive)
        XCTAssertEqual(report.providers[1].detection?.requirement, .unknown)
        XCTAssertEqual(report.collection.summary?.outcomes?.first?.outcome, .unknown)
        XCTAssertNotEqual(HeaderFacts.result(report).kind, .success, "an unknown outcome is not fresh")
    }

    private func roundTrip(_ mutate: (inout [String: Any]) -> Void) throws -> Report {
        var json = try JSONSerialization.jsonObject(with: try legacyBase().encoded()) as! [String: Any]
        mutate(&json)
        return try Report.decode(try JSONSerialization.data(withJSONObject: json))
    }

    func testDecodesNewOptionalFields() throws {
        let report = try roundTrip { json in
            var providers = json["providers"] as! [[String: Any]]
            providers[0]["tracking"] = "auto"
            providers[0]["tracking_effective"] = "inactive"
            providers[0]["detection"] = ["installed": false, "requirement": "executable", "saved_data": true]
            providers[0]["last_activity_at"] = "2026-09-29T15:00:00Z"
            providers[1]["detection"] = ["installed": NSNull(), "requirement": "none", "saved_data": false]
            json["providers"] = providers
            var collector = json["collector"] as! [String: Any]
            var capabilities = collector["capabilities"] as? [String: Any] ?? [:]
            capabilities["tracking_policy"] = true
            collector["capabilities"] = capabilities
            json["collector"] = collector
            var collection = json["collection"] as! [String: Any]
            var summary = collection["summary"] as! [String: Any]
            summary["providers_expected"] = 4
            summary["providers_fresh"] = 2
            summary["providers_stale"] = 1
            summary["outcomes"] = [["id": "codex", "outcome": "fresh"], ["id": "grok", "outcome": "partly"]]
            collection["summary"] = summary
            json["collection"] = collection
        }
        let first = report.providers[0]
        XCTAssertEqual(first.tracking, .auto)
        XCTAssertEqual(first.trackingEffective, .inactive)
        XCTAssertEqual(first.detection, Detection(installed: false, requirement: .executable, savedData: true))
        XCTAssertEqual(first.lastActivityAt, ReportDate.parse("2026-09-29T15:00:00Z"))
        XCTAssertFalse(first.isOverviewVisible)
        XCTAssertEqual(report.providers[1].detection?.installed, nil)
        XCTAssertEqual(report.providers[1].detection?.requirement, Detection.Requirement.none)
        XCTAssertEqual(report.collector.capabilities?.trackingPolicy, true)
        let summary = try XCTUnwrap(report.collection.summary)
        XCTAssertEqual(summary.providersExpected, 4)
        XCTAssertEqual(summary.providersFresh, 2)
        XCTAssertEqual(summary.providersStale, 1)
        XCTAssertEqual(summary.outcomes?.last?.outcome, .partly)
    }

    func testOldReportsFallBackToEnabled() throws {
        let report = try roundTrip { json in
            var providers = json["providers"] as! [[String: Any]]
            providers[0]["enabled"] = false
            json["providers"] = providers
        }
        XCTAssertNil(report.providers[0].tracking)
        XCTAssertNil(report.collection.summary?.providersExpected)
        XCTAssertEqual(report.providers[0].trackingMode, .off)
        XCTAssertEqual(report.providers[0].effectiveTracking, .inactive)
        XCTAssertFalse(report.providers[0].isOverviewVisible)
        XCTAssertEqual(report.providers[1].trackingMode, .on)
        XCTAssertEqual(report.providers[1].effectiveTracking, .active)
        XCTAssertTrue(report.providers[1].isOverviewVisible)
    }

    func testTrackingConfigChanges() throws {
        let report = try legacyBase().adding(instance: "claude-az", copying: "claude", name: "Claude AZ")
        XCTAssertEqual(ConfigChange.tracking(report.provider("claude")!, .auto).assignment, "providers.claude.tracking=\"auto\"")
        XCTAssertEqual(ConfigChange.tracking(report.provider("claude-az")!, .off).assignment, "instances.claude-az.tracking=\"off\"")
        XCTAssertEqual(TrackingMode.off.label, "Off")
        XCTAssertEqual(TrackingMode.on.label, "On")
    }
}

@MainActor
final class TrackingVisibilityTests: XCTestCase {
    private func store(_ report: Report, preferences: PreferencesStoring = InMemoryPreferences()) -> AppStore {
        let store = AppStore(environment: environment(), reporter: StaticReport(report: report), collector: NoCollector(),
                             clock: { report.generatedAt }, preferences: preferences)
        store.apply(report, cached: false)
        return store
    }

    func testOffAndAutoInactiveHiddenExplicitOnVisible() throws {
        var report = try legacyBase()
        report.set("codex", policy(.off, .inactive, enabled: false))
        report.set("claude", policy(.auto, .inactive, enabled: false))
        report.set("grok", policy(.on, .active))
        report.set("gemini_cli", policy(.auto, .active))
        let ids = store(report).cardProviders.map(\.id)
        XCTAssertFalse(ids.contains("codex"))
        XCTAssertFalse(ids.contains("claude"))
        XCTAssertTrue(ids.contains("grok"))
        XCTAssertTrue(ids.contains("gemini_cli"))
    }

    func testDisablingOneClaudeInstanceLeavesTheOther() throws {
        var report = try legacyBase().adding(instance: "claude-az", copying: "claude", name: "Claude AZ")
        report.set("claude", policy(.off, .inactive, enabled: false))
        let ids = store(report).cardProviders.map(\.id)
        XCTAssertFalse(ids.contains("claude"))
        XCTAssertTrue(ids.contains("claude-az"))
    }

    func testAllInactiveAsksToChooseProviders() throws {
        var report = try legacyBase()
        for id in report.providers.map(\.id) { report.set(id, policy(.auto, .inactive, enabled: false)) }
        let subject = store(report)
        XCTAssertTrue(subject.cardProviders.isEmpty)
        XCTAssertTrue(subject.needsProviderChoice)
        report.set("codex", policy(.on, .active))
        XCTAssertFalse(store(report).needsProviderChoice)
    }

    func testFirstRunPendingIsAwaitingDetectionNotChooseProviders() throws {
        var report = try legacyBase()
        for id in report.providers.map(\.id) { report.set(id, policy(.auto, .inactive, enabled: false)) }
        report.set("codex", policy(.auto, .pending, enabled: false))
        let subject = store(report)
        XCTAssertTrue(subject.cardProviders.isEmpty, "pending stays off the cards")
        XCTAssertTrue(subject.isAwaitingDetection)
        XCTAssertFalse(subject.needsProviderChoice)
    }
}

@MainActor
final class DetectionNoticeTests: XCTestCase {
    private func uninstalled(_ report: inout Report, _ id: String, requirement: Detection.Requirement = .executable) {
        report.set(id) {
            $0.tracking = .auto
            $0.trackingEffective = .inactive
            $0.detection = Detection(installed: false, requirement: requirement, savedData: true)
        }
    }

    func testConditions() throws {
        var report = try legacyBase()
        uninstalled(&report, "codex")
        report.set("claude") { $0.tracking = .auto; $0.detection = Detection(installed: false, requirement: .executable, savedData: false) }
        report.set("grok") { $0.tracking = .on; $0.detection = Detection(installed: false, requirement: .executable, savedData: true) }
        report.set("gemini_cli") { $0.tracking = .auto; $0.detection = Detection(installed: nil, requirement: .none, savedData: true) }
        let notices = DetectionNotice.notices(report, acknowledged: [])
        XCTAssertEqual(notices.map(\.providerId), ["codex"])
        XCTAssertEqual(notices[0].title, "Saved usage found. We couldn't find this CLI.")
        XCTAssertEqual(notices[0].actions.map(\.label), ["Keep history", "Review provider settings"])
        XCTAssertEqual(notices[0].defaultAction, .keepHistory)
    }

    func testOneNoticePerProvider() throws {
        var report = try legacyBase()
        uninstalled(&report, "codex")
        report.providers.append(report.provider("codex")!)
        XCTAssertEqual(DetectionNotice.notices(report, acknowledged: []).count, 1)
    }

    func testAcknowledgementPersistsAndReappearsOnChange() throws {
        var report = try legacyBase()
        uninstalled(&report, "codex")
        let preferences = InMemoryPreferences()
        let first = AppStore(environment: environment(), reporter: StaticReport(report: report), collector: NoCollector(),
                             clock: { report.generatedAt }, preferences: preferences)
        first.apply(report, cached: false)
        XCTAssertEqual(first.detectionNotices.count, 1)
        first.acknowledgeDetection(first.detectionNotices[0])
        XCTAssertTrue(first.detectionNotices.isEmpty)

        // Restart: still acknowledged.
        let second = AppStore(environment: environment(), reporter: StaticReport(report: report), collector: NoCollector(),
                              clock: { report.generatedAt }, preferences: preferences)
        second.apply(report, cached: false)
        XCTAssertTrue(second.detectionNotices.isEmpty)

        // Transient unknown probe, a report without detection, and a cached report keep it.
        var unknown = report
        unknown.set("codex") { $0.detection?.installed = nil }
        second.apply(unknown, cached: false)
        var noDetection = report
        noDetection.set("codex") { $0.detection = nil }
        second.apply(noDetection, cached: false)
        var found = report
        found.set("codex") { $0.detection?.installed = true }
        second.apply(found, cached: true)
        second.apply(report, cached: false)
        XCTAssertTrue(second.detectionNotices.isEmpty, "still acknowledged after a transient unknown")

        // CLI found, then missing again: reappears.
        second.apply(found, cached: false)
        second.apply(report, cached: false)
        XCTAssertEqual(second.detectionNotices.map(\.providerId), ["codex"])
    }
}
