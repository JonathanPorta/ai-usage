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

        // Settings go through the real collector `configure`: atomic, validated, other keys kept.
        let configURL = root.appendingPathComponent("config.json")
        let store2 = AppStore(environment: environment,
                              reporter: LiveReportClient(environment: environment, runner: runner),
                              collector: LiveCollectorClient(environment: environment, runner: runner),
                              config: LiveConfigWriter(environment: environment, runner: runner))
        await store2.applySettings([.pollPaused(true), .monthlySubscription("grok", 30), .providerEnabled("gemini_cli", false)])
        XCTAssertEqual(store2.action, .done("Settings saved"))
        let written = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: configURL)) as? [String: Any])
        XCTAssertEqual(written["poll_paused"] as? Bool, true)
        let providers = try XCTUnwrap(written["providers"] as? [String: [String: Any]])
        XCTAssertEqual(providers["grok"]?["monthly_subscription_usd"] as? Double, 30)
        XCTAssertEqual(providers["gemini_cli"]?["enabled"] as? Bool, false)
        XCTAssertNotNil(providers["claude"]?["projects_dir"], "unrelated settings are preserved")
        XCTAssertEqual(store2.report?.schedule.pauseRequested, true)
        XCTAssertEqual(store2.report?.provider("grok")?.costs.subscription?.monthlyUsd, 30)

        await store2.applySettings([.pollIntervalSeconds(5)])
        if case let .failed(message) = store2.action {
            XCTAssertTrue(message.contains("at least 60"), message)
        } else { XCTFail("invalid interval must be rejected") }
        let rejected = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: configURL)) as? [String: Any])
        XCTAssertEqual(rejected["poll_interval_seconds"] as? Int, 3600, "a rejected setting leaves config.json unchanged")
    }
}
