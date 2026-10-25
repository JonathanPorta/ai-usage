import Foundation
import XCTest
@testable import AIUsageCore

/// Local plugins (phase 3): decoding, the Settings switch and its config write.
/// The collector re-validates every change it is sent.
final class LocalPluginTests: XCTestCase {
    func testTheLocalPluginFixtureHasALocalPluginLikeABuiltIn() throws {
        let report = try Report.localPluginFixture()
        XCTAssertEqual(report.providers.map(\.id), ["codex", "claude", "antigravity", "grok", "gemini_cli", "example"])
        let plugin = try XCTUnwrap(report.provider("example"))
        XCTAssertTrue(plugin.isLocalPlugin)
        XCTAssertFalse(plugin.isAddedAccount, "its id is its type, like a built-in")
        XCTAssertEqual(plugin.canonicalType, "example")
        XCTAssertEqual(plugin.name, "Example")
        XCTAssertTrue(plugin.isActive)
        XCTAssertEqual(plugin.today?.total, 340_437)
        let window = try XCTUnwrap(plugin.activeWindows.first)
        XCTAssertEqual(window.basis, .used)
        XCTAssertTrue(report.collector.supportsLocalPlugins)
        for builtIn in report.providers where builtIn.id != "example" {
            XCTAssertFalse(builtIn.isLocalPlugin)
            XCTAssertNil(builtIn.external)
        }
    }

    func testReportsWithoutLocalPluginsDecodeUnchanged() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "report-collector-2.2.0", withExtension: "json", subdirectory: "Fixtures"))
        let older = try Report.decode(Data(contentsOf: url))
        XCTAssertNil(older.collector.capabilities?.externalPlugins)
        XCTAssertFalse(older.collector.supportsLocalPlugins)
        XCTAssertFalse(older.providers.contains { $0.isLocalPlugin })
        for report in [try Report.fixture(), try Report.twoAccountFixture()] {
            XCTAssertFalse(report.providers.contains { $0.isLocalPlugin })
            XCTAssertTrue(report.collector.supportsLocalPlugins, "both fixtures come from collector 2.4.0")
        }
    }

    func testTheSwitchWritesTheProvidersEntryAndIsHiddenWithoutTheCapability() throws {
        var report = try Report.localPluginFixture()
        let plugin = try XCTUnwrap(report.provider("example"))
        XCTAssertEqual(ConfigChange.enabling(plugin, false).assignment, "providers.example.enabled=false")
        XCTAssertEqual(ConfigChange.pricing(plugin, 12).assignment, "providers.example.monthly_subscription_usd=12")
        XCTAssertTrue(Presentation.showsToggle(plugin, in: report))
        report.collector.capabilities?.externalPlugins = false
        XCTAssertFalse(Presentation.showsToggle(plugin, in: report))
        report.collector.capabilities?.externalPlugins = nil
        XCTAssertFalse(Presentation.showsToggle(plugin, in: report))
        XCTAssertTrue(Presentation.showsToggle(try XCTUnwrap(report.provider("codex")), in: report),
                      "built-ins keep their switch")
    }
}
