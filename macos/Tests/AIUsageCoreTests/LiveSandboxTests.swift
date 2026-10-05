import Foundation
import XCTest
@testable import AIUsageCore

/// End to end through real processes, isolated in a temporary sandbox:
/// report script → store; Collect now → the collector's real `once` → refreshed store.
/// Runs when AI_USAGE_REPO_ROOT is set (as `make app-test` does); never touches ~/.ai-usage.
@MainActor
final class LiveSandboxTests: XCTestCase {
    func testCollectNowRunsRealOnceAndRefreshesTheSharedStore() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let repo = env["AI_USAGE_REPO_ROOT"] else { throw XCTSkip("set AI_USAGE_REPO_ROOT to run sandbox integration") }
        let python = URL(fileURLWithPath: env["AI_USAGE_TEST_PYTHON"] ?? "/usr/bin/python3")
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("ai-usage-sandbox-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let runner = SystemProcessRunner()
        let isolated = ["HOME": root.path, "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "TZ": "America/Denver"]
        let made = try await runner.run(python, arguments: ["\(repo)/ai_usage_fixtures.py", "sandbox", root.path],
                                        environment: isolated, timeout: 120)
        XCTAssertEqual(made.exitCode, 0, made.stderr)

        let environment = CollectorEnvironment(
            python: python,
            collectorScript: URL(fileURLWithPath: "\(repo)/ai_usage_service.py"),
            configPath: root.appendingPathComponent("config.json"),
            reportScript: URL(fileURLWithPath: "\(repo)/ai_usage_report.py"),
            skipServiceProbe: true,
            stateDirectory: root.appendingPathComponent("app-state"),
            childEnvironment: isolated)
        let store = AppStore(environment: environment,
                             reporter: LiveReportClient(environment: environment, runner: runner),
                             collector: LiveCollectorClient(environment: environment, runner: runner))
        await store.refresh()
        let before = try XCTUnwrap(store.report, store.refreshError ?? "no report")
        let beforeAttempt = try XCTUnwrap(before.collection.lastAttemptAt)
        let revision = store.revision

        let run = await store.collectNow()
        XCTAssertNotNil(run)
        XCTAssertNotEqual(run?.outcome, .failed, run?.message ?? "")
        let after = try XCTUnwrap(store.report)
        XCTAssertEqual(store.revision, revision + 1)
        XCTAssertGreaterThan(try XCTUnwrap(after.collection.lastAttemptAt), beforeAttempt,
                             "the refreshed report must include the new one-time check")
        // The sandbox points every provider at missing binaries and empty dirs:
        // cached measurements survive a check that found nothing new.
        XCTAssertEqual(after.provider("grok")?.costs.reported?.amountUsd, before.provider("grok")?.costs.reported?.amountUsd)
        XCTAssertNil(store.refreshError)
    }
}
