import AIUsageCore
import AIUsageDesign
import AppKit
import SwiftUI

/// Settings › Providers › "Add Claude account…": a second Claude Code login, read
/// as its own provider. The collector's `instance add` validates and saves it.
///
/// States: unsupported (the installed collector predates added accounts), closed,
/// editing (with specific problems), saving, saved (closes; the banner confirms),
/// and failed (the form stays filled, with the collector's reason).
struct AddAccountView: View {
    @Environment(AppStore.self) private var store
    let report: Report

    @State private var open: Bool
    @State private var draft: AccountDraft
    /// Whether the user has finished with a field, so empty-field hints wait for them.
    @State private var touched: Bool
    private let folderCheck: (URL?) -> Bool

    init(report: Report, open: Bool = false, draft: AccountDraft = AccountDraft(), touched: Bool = false,
         folderCheck: @escaping (URL?) -> Bool = { AccountDraft.folderHasClaudeConfig($0) }) {
        self.report = report
        _open = State(initialValue: open)
        _draft = State(initialValue: draft)
        _touched = State(initialValue: touched)
        self.folderCheck = folderCheck
    }

    private var taken: Set<String> { Set(report.providers.map(\.id)) }
    private var problems: [AccountDraft.Problem] {
        draft.problems(taken: taken, folderHasClaudeConfig: folderCheck(draft.folder))
    }
    private var saving: Bool {
        if case .running = store.action { return true }
        return false
    }

    var body: some View {
        if !report.collector.supportsAddedAccounts {
            unsupported
        } else if open {
            form
        } else {
            Button {
                draft = AccountDraft()
                touched = false
                // An earlier, unrelated failure must not read as this form's.
                if case .failed = store.action { store.dismissAction() }
                open = true
            } label: { Label("Add Claude account…", systemImage: Symbols.add) }
                .buttonStyle(.link).font(.auBody)
                .disabled(store.isActing)
        }
    }

    private var unsupported: some View {
        let installed = report.collector.installedVersion ?? "none"
        return Text("Adding a Claude account needs collector 2.3.0 or newer (installed: \(installed)). Reinstall it from the repository with python3 ai_usage_service.py install.")
            .font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: T.Space.s3) {
            Text("Add a Claude account").font(.auBody).bold().accessibilityAddTraits(.isHeader)
            Text("For another Claude Code login with its own config folder (its CLAUDE_CONFIG_DIR). It is shown as its own provider; your other accounts are unchanged.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
            field("Name") {
                TextField("Claude (work)", text: Binding(get: { draft.label }, set: { draft.setLabel($0, taken: taken) }))
                    .textFieldStyle(.roundedBorder).controlSize(.small)
                    .onSubmit { touched = true }
                    .accessibilityLabel("Account name")
            }
            field("Id") {
                TextField("claude-work", text: Binding(get: { draft.id }, set: { draft.setId($0) }))
                    .textFieldStyle(.roundedBorder).controlSize(.small).font(.system(.caption, design: .monospaced))
                    .accessibilityLabel("Account id")
            }
            Text("The id names this account in usage.csv and can’t be changed later.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color)
            field("Config folder") {
                HStack(spacing: T.Space.s2) {
                    Text(draft.folder.map { abbreviated($0) } ?? "None chosen")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(draft.folder == nil ? T.Color.muted.color : T.Color.text.color)
                        .lineLimit(1).truncationMode(.middle)
                    Spacer()
                    Button("Choose…", action: chooseFolder).controlSize(.small)
                }
            }
            ForEach(visibleProblems, id: \.message) { problem in
                Label(problem.message, systemImage: Symbols.failure)
                    .font(.auCaption).foregroundStyle(T.Color.danger.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if case let .failed(reason) = store.action {
                Label("Not added: \(reason)", systemImage: Symbols.failure)
                    .font(.auCaption).foregroundStyle(T.Color.danger.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: T.Space.s2) {
                if saving {
                    ProgressView().controlSize(.mini)
                    Text("Adding \(draft.label)…").font(.auCaption)
                }
                Spacer()
                Button("Cancel") { open = false }.controlSize(.small).disabled(saving)
                Button("Add account", action: submit)
                    .controlSize(.small).buttonStyle(.borderedProminent)
                    .disabled(!problems.isEmpty || store.isActing)
            }
            Text("Its monthly price can be entered here after it is added.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color)
        }
        .padding(T.Space.s3)
        .background(RoundedRectangle(cornerRadius: 8).strokeBorder(T.Color.line.color))
    }

    /// Empty fields aren't errors until the user has had a chance to fill them.
    private var visibleProblems: [AccountDraft.Problem] {
        problems.filter { touched || ($0 != .emptyLabel && $0 != .noFolder) }
    }

    private func field<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.auCaption).foregroundStyle(T.Color.muted.color)
            content()
        }
    }

    private func abbreviated(_ url: URL) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return url.path.hasPrefix(home + "/") ? "~" + url.path.dropFirst(home.count) : url.path
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true  // account folders such as ~/.claude-work are hidden
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser
        panel.message = "Choose the Claude Code config folder for this account (the folder holding its .claude.json)."
        panel.prompt = "Choose"
        if panel.runModal() == .OK {
            draft.folder = panel.url
            touched = true
        }
    }

    private func submit() {
        touched = true
        guard problems.isEmpty, let request = draft.request else { return }
        Task {
            if await store.addAccount(request) { open = false }
        }
    }
}
