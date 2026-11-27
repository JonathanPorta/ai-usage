import XCTest
@testable import AIUsageCore

final class LoginItemPolicyTests: XCTestCase {
    func testNormalRunIsManaged() {
        XCTAssertEqual(LoginItemPolicy.mode(arguments: ["AIUsage"], environment: [:]), .managed)
        XCTAssertEqual(LoginItemPolicy.mode(arguments: ["AIUsage"], environment: ["AI_USAGE_SERVICE": ""]), .managed)
    }

    func testOffscreenRunsUseFixture() {
        XCTAssertEqual(LoginItemPolicy.mode(arguments: ["AIUsage", "--snapshot", "/x"], environment: [:]), .offscreenFixture)
        XCTAssertEqual(LoginItemPolicy.mode(arguments: ["AIUsage", "--measure"], environment: ["AI_USAGE_SERVICE": "skip"]), .offscreenFixture)
    }

    func testSandboxRunIsUnavailable() {
        XCTAssertEqual(LoginItemPolicy.mode(arguments: ["AIUsage"], environment: ["AI_USAGE_SERVICE": "skip"]), .unavailable)
    }
}
