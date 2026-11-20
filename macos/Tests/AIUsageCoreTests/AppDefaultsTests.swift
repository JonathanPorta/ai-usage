import Foundation
import XCTest
@testable import AIUsageCore

final class AppDefaultsTests: XCTestCase {
    func testNoOverrideUsesStandardDefaults() {
        XCTAssertNil(AppDefaults.sandboxStateDirectory(environment: [:]))
        XCTAssertNil(AppDefaults.sandboxStateDirectory(environment: ["AI_USAGE_STATE_DIR": ""]))
        XCTAssertTrue(AppDefaults.store(environment: [:]) === UserDefaults.standard)
    }

    func testStateDirOverrideUsesIsolatedPlistUnderStateDir() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("AppDefaultsTests-\(UUID().uuidString)/app-state")
        defer { try? FileManager.default.removeItem(at: dir.deletingLastPathComponent()) }
        let env = ["AI_USAGE_STATE_DIR": dir.path]
        XCTAssertEqual(AppDefaults.sandboxStateDirectory(environment: env), dir)

        let store = AppDefaults.store(environment: env)
        XCTAssertFalse(store === UserDefaults.standard)
        let key = "appDefaultsTestKey-\(UUID().uuidString)"
        store.set("sandbox", forKey: key)
        store.synchronize()
        XCTAssertEqual(store.string(forKey: key), "sandbox")
        XCTAssertNil(UserDefaults.standard.object(forKey: key))
        XCTAssertTrue(FileManager.default.fileExists(atPath: dir.appendingPathComponent("preferences.plist").path))
    }
}
