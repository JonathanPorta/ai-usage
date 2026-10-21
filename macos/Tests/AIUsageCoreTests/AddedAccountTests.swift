import Foundation
import XCTest
@testable import AIUsageCore

/// Added accounts (phase 2): decoding, config writes, the add-account form's rules
/// and the store's add action. The collector re-validates everything it is sent.
final class AddedAccountTests: XCTestCase {
    func testAReportFromCollector220StillDecodes() throws {
        // Saved before phase 2: no `type` on providers, no `instances` capability.
        let url = try XCTUnwrap(Bundle.module.url(forResource: "report-collector-2.2.0", withExtension: "json", subdirectory: "Fixtures"))
        let data = try Data(contentsOf: url)
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains("\"type\""))
        let report = try Report.decode(data)
        XCTAssertFalse(report.providers.isEmpty)
        for provider in report.providers {
            XCTAssertNil(provider.type)
            XCTAssertEqual(provider.canonicalType, provider.id)
            XCTAssertFalse(provider.isAddedAccount)
        }
        XCTAssertNil(report.collector.capabilities?.instances)
        XCTAssertFalse(report.collector.supportsAddedAccounts)
    }

    func testTheTwoAccountFixtureHasAnAddedClaudeAccount() throws {
        let report = try Report.twoAccountFixture()
        XCTAssertEqual(report.providers.map(\.id), ["codex", "claude", "antigravity", "grok", "gemini_cli", "claude-az"])
        let account = try XCTUnwrap(report.provider("claude-az"))
        XCTAssertEqual(account.type, "claude")
        XCTAssertEqual(account.name, "Claude (work)")
        XCTAssertTrue(account.isAddedAccount)
        XCTAssertFalse(try XCTUnwrap(report.provider("claude")).isAddedAccount)
        XCTAssertTrue(report.collector.supportsAddedAccounts)
        XCTAssertFalse(account.activeWindows.isEmpty, "its own plan limits")
        XCTAssertTrue(try fixture().collector.supportsAddedAccounts, "the main fixture comes from collector 2.3.0 too")
    }

    func testSettingsChangesWriteAnAddedAccountsInstanceEntry() throws {
        let report = try Report.twoAccountFixture()
        let account = try XCTUnwrap(report.provider("claude-az"))
        let builtIn = try XCTUnwrap(report.provider("claude"))
        XCTAssertEqual(ConfigChange.enabling(account, false).assignment, "instances.claude-az.enabled=false")
        XCTAssertEqual(ConfigChange.pricing(account, 20).assignment, "instances.claude-az.monthly_subscription_usd=20")
        XCTAssertEqual(ConfigChange.pricing(account, nil).assignment, "instances.claude-az.monthly_subscription_usd=null")
        XCTAssertEqual(ConfigChange.enabling(builtIn, true).assignment, "providers.claude.enabled=true")
        XCTAssertEqual(ConfigChange.pricing(builtIn, 19.5).assignment, "providers.claude.monthly_subscription_usd=19.5")
    }

    func testTheProposedIdComesFromTheLabelAndIsUnique() {
        XCTAssertEqual(AccountDraft.proposedId(for: "Claude (work)", taken: []), "claude-work")
        XCTAssertEqual(AccountDraft.proposedId(for: "Work", taken: []), "claude-work")
        XCTAssertEqual(AccountDraft.proposedId(for: "Ünïcode Account!", taken: []), "claude-unicode-account")
        XCTAssertEqual(AccountDraft.proposedId(for: "", taken: []), "claude-2")
        XCTAssertEqual(AccountDraft.proposedId(for: "Claude", taken: []), "claude-2", "never the built-in id")
        XCTAssertEqual(AccountDraft.proposedId(for: "Work", taken: ["claude-work", "claude-work-2"]), "claude-work-3")
        let long = AccountDraft.proposedId(for: String(repeating: "very long name ", count: 6), taken: [])
        XCTAssertLessThanOrEqual(long.count, 32)
        XCTAssertTrue(AccountDraft.isValidId(long), long)
    }

    func testTheLabelProposesTheIdUntilTheIdIsEdited() {
        var draft = AccountDraft()
        draft.setLabel("Claude (work)", taken: [])
        XCTAssertEqual(draft.id, "claude-work")
        draft.setId("cw")
        draft.setLabel("Claude (personal)", taken: [])
        XCTAssertEqual(draft.id, "cw", "an edited id is the user's")
        XCTAssertEqual(draft.label, "Claude (personal)")
    }

    func testEachProblemTheFormReports() {
        let folder = URL(fileURLWithPath: "/tmp/account")
        let taken: Set<String> = ["codex", "claude", "claude-az"]
        func problems(_ draft: AccountDraft, claude: Bool = true) -> [AccountDraft.Problem] {
            draft.problems(taken: taken, folderHasClaudeConfig: claude)
        }
        XCTAssertEqual(problems(AccountDraft(label: "Work", id: "claude-work", folder: folder)), [])
        XCTAssertEqual(problems(AccountDraft(label: " ", id: "claude-work", folder: folder)), [.emptyLabel])
        XCTAssertEqual(problems(AccountDraft(label: "W", id: "Claude Work", folder: folder)), [.invalidId])
        XCTAssertEqual(problems(AccountDraft(label: "W", id: "grok", folder: folder)), [.builtInId])
        XCTAssertEqual(problems(AccountDraft(label: "W", id: "claude-az", folder: folder)), [.duplicateId])
        XCTAssertEqual(problems(AccountDraft(label: "W", id: "claude-work")), [.noFolder])
        XCTAssertEqual(problems(AccountDraft(label: "W", id: "claude-work", folder: folder), claude: false), [.folderNotClaude])
        XCTAssertEqual(problems(AccountDraft(label: "", id: "", folder: nil), claude: false), [.emptyLabel, .invalidId, .noFolder])
    }

    func testTheFolderCheckLooksForClaudeCodesConfigFile() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("ai-usage-account-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        XCTAssertFalse(AccountDraft.folderHasClaudeConfig(folder))
        try Data("{}".utf8).write(to: folder.appendingPathComponent(".claude.json"))
        XCTAssertTrue(AccountDraft.folderHasClaudeConfig(folder))
        XCTAssertFalse(AccountDraft.folderHasClaudeConfig(nil))
    }

    func testTheRequestRunsInstanceAddWithAnAbsoluteFolderAndNoPrice() throws {
        let draft = AccountDraft(label: " Claude (work) ", id: "claude-work", folder: URL(fileURLWithPath: "/Users/you/.claude-work"))
        let request = try XCTUnwrap(draft.request)
        XCTAssertEqual(request.arguments(configPath: "/Users/you/.ai-usage/config.json"), [
            "instance", "add", "--plugin", "claude", "--id", "claude-work", "--label", "Claude (work)",
            "--config-dir", "/Users/you/.claude-work", "--config", "/Users/you/.ai-usage/config.json",
        ])
        XCTAssertNil(AccountDraft(label: "x", id: "claude-x").request)
    }

    @MainActor
    func testTheStoreReportsAddedAndFailedAccounts() async throws {
        let config = AccountSpy()
        let store = AppStore(environment: environment(), reporter: SpyReporter([.success(try Report.twoAccountFixture()),
                                                                               .success(try Report.twoAccountFixture())]),
                             collector: SpyCollector(), service: SpyService(), config: config)
        let request = AccountRequest(plugin: "claude", id: "claude-work", label: "Claude (work)", configDir: "/tmp/w")
        let added = await store.addAccount(request)
        XCTAssertTrue(added)
        XCTAssertEqual(config.added, [request])
        XCTAssertEqual(store.action, .done("Claude (work) added. It is read at the next check."))
        config.error = ClientError.processFailed("instances.claude-work: the id is used twice")
        store.dismissAction()
        let again = await store.addAccount(request)
        XCTAssertFalse(again)
        XCTAssertEqual(store.action, .failed("instances.claude-work: the id is used twice"))
    }

    func testWritersWithoutAccountSupportRefuse() async {
        do {
            try await NoConfigWriter().addAccount(AccountRequest(plugin: "claude", id: "c-2", label: "C", configDir: "/tmp"))
            XCTFail("expected a refusal")
        } catch {}
    }
}

final class AccountSpy: ConfigWriting, @unchecked Sendable {
    var added: [AccountRequest] = []
    var error: Error?
    func apply(_ changes: [ConfigChange]) async throws {}
    func addAccount(_ request: AccountRequest) async throws {
        if let error { throw error }
        added.append(request)
    }
}
