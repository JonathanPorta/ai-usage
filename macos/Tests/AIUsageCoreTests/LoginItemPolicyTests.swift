import XCTest
@testable import AIUsageCore

final class LoginItemPolicyTests: XCTestCase {
    private let home = "/Users/someone"

    private func mode(_ bundle: String, _ arguments: [String] = ["AIUsage"],
                      _ environment: [String: String] = [:]) -> LoginItemPolicy.Mode {
        LoginItemPolicy.mode(arguments: arguments, environment: environment, bundlePath: bundle, homeDirectory: home)
    }

    func testCopyInUserApplicationsIsManaged() {
        XCTAssertEqual(mode("/Users/someone/Applications/AI Usage.app"), .managed)
        XCTAssertEqual(mode("/Users/someone/Applications/AI Usage.app", ["AIUsage"], ["AI_USAGE_SERVICE": ""]), .managed)
    }

    func testCopyInApplicationsIsManaged() {
        XCTAssertEqual(mode("/Applications/AI Usage.app"), .managed)
    }

    func testBuildDirectoryLaunchIsUnavailable() {
        XCTAssertEqual(mode("/Users/someone/devel/worktrees/ai-usage/task/macos/build/AI Usage.app"), .unavailable)
        XCTAssertEqual(mode("/Users/someone/Applications/Sub/AI Usage.app"), .unavailable)
    }

    func testSandboxFromInstalledPathIsUnavailable() {
        XCTAssertEqual(mode("/Applications/AI Usage.app", ["AIUsage"], ["AI_USAGE_SERVICE": "skip"]), .unavailable)
    }

    func testOffscreenRunsUseFixtureFromAnyPath() {
        XCTAssertEqual(mode("/Applications/AI Usage.app", ["AIUsage", "--snapshot", "/x"]), .offscreenFixture)
        XCTAssertEqual(mode("/tmp/build/AI Usage.app", ["AIUsage", "--measure"], ["AI_USAGE_SERVICE": "skip"]), .offscreenFixture)
    }
}
