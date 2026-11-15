import XCTest
@testable import AIUsageCore

final class SettingsTrackingTests: XCTestCase {
    func testDetectionText() throws {
        var p = try legacyBase().providers[0]
        XCTAssertNil(SettingsTracking.detectionText(p))
        p.detection = Detection(installed: true, requirement: .executable, savedData: nil)
        XCTAssertEqual(SettingsTracking.detectionText(p), "Installed")
        p.detection = Detection(installed: false, requirement: .executable, savedData: true)
        XCTAssertEqual(SettingsTracking.detectionText(p), "Not found")
        p.detection = Detection(installed: nil, requirement: .executable, savedData: nil)
        XCTAssertEqual(SettingsTracking.detectionText(p), "Detection unknown")
        p.detection = Detection(installed: nil, requirement: Detection.Requirement.none, savedData: nil)
        XCTAssertNil(SettingsTracking.detectionText(p))
    }

    func testStatusText() throws {
        var p = try legacyBase().providers[0]
        p.tracking = .auto
        p.trackingEffective = .active
        XCTAssertEqual(SettingsTracking.statusText(p, shown: .auto, saving: false), "Auto · tracking")
        p.trackingEffective = .inactive
        XCTAssertEqual(SettingsTracking.statusText(p, shown: .auto, saving: false), "Auto · not tracking")
        XCTAssertNil(SettingsTracking.statusText(p, shown: .on, saving: false))
        XCTAssertEqual(SettingsTracking.statusText(p, shown: .off, saving: true), "Saving…")
        XCTAssertEqual(SettingsTracking.shortLabel(.auto), "Auto")
    }
}
