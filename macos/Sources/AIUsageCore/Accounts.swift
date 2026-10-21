import Foundation

/// An added Claude account being created in Settings ("Add account…").
///
/// The collector's `instance add` is the authority: it validates again under the
/// config lock and refuses what an older installed collector would ignore. This
/// model only gives the form immediate, specific feedback.
public struct AccountDraft: Equatable, Sendable {
    public enum Problem: Equatable, Sendable {
        case emptyLabel
        case invalidId
        case builtInId
        case duplicateId
        case noFolder
        case folderNotClaude

        public var message: String {
            switch self {
            case .emptyLabel: return "Enter a name for this account."
            case .invalidId: return "Use a lowercase letter, then up to 31 letters, digits, - or _."
            case .builtInId: return "That id belongs to a built-in provider. Choose another."
            case .duplicateId: return "Another provider already uses that id."
            case .noFolder: return "Choose the account's Claude Code config folder."
            case .folderNotClaude: return "That folder has no .claude.json. Choose the folder Claude Code uses for this account (its CLAUDE_CONFIG_DIR)."
            }
        }
    }

    /// Ids of the bundled providers: never available to an added account.
    public static let builtInIds: Set<String> = ["codex", "claude", "antigravity", "gemini_cli", "grok"]
    public static let plugin = "claude"

    public var label: String
    public var id: String
    /// Once the user edits the id, the label no longer rewrites it.
    public var idEdited: Bool
    public var folder: URL?

    public init(label: String = "", id: String = "", idEdited: Bool = false, folder: URL? = nil) {
        self.label = label
        self.id = id
        self.idEdited = idEdited
        self.folder = folder
    }

    /// Updates the label and, until the id has been edited, the proposed id.
    public mutating func setLabel(_ value: String, taken: Set<String>) {
        label = value
        if !idEdited { id = Self.proposedId(for: value, taken: taken) }
    }

    public mutating func setId(_ value: String) {
        id = value
        idEdited = true
    }

    /// `claude-<slug of the label>`, made unique with a numeric suffix.
    public static func proposedId(for label: String, taken: Set<String>) -> String {
        let folded = label.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: Locale(identifier: "en_US_POSIX"))
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789")
        var slug = ""
        for scalar in folded.lowercased().unicodeScalars {
            if allowed.contains(scalar) {
                slug.unicodeScalars.append(scalar)
            } else if !slug.isEmpty, slug.last != "-" {
                slug.append("-")
            }
        }
        while slug.hasSuffix("-") { slug.removeLast() }
        if slug.hasPrefix("claude") { slug = String(slug.dropFirst("claude".count)).trimmingCharacters(in: CharacterSet(charactersIn: "-")) }
        var base = slug.isEmpty ? "claude-2" : "claude-\(slug)"
        if base.count > 32 { base = String(base.prefix(32)).trimmingCharacters(in: CharacterSet(charactersIn: "-_")) }
        var candidate = base
        var suffix = 2
        while taken.contains(candidate) || builtInIds.contains(candidate) {
            let tail = "-\(suffix)"
            candidate = String(base.prefix(32 - tail.count)) + tail
            suffix += 1
        }
        return candidate
    }

    public static func isValidId(_ id: String) -> Bool {
        id.range(of: "^[a-z][a-z0-9_-]{0,31}$", options: .regularExpression) != nil
    }

    /// Problems in display order; empty means the draft can be submitted.
    /// `folderHasClaudeConfig` reports whether the chosen folder holds `.claude.json`.
    public func problems(taken: Set<String>, folderHasClaudeConfig: Bool) -> [Problem] {
        var found: [Problem] = []
        if label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { found.append(.emptyLabel) }
        if Self.builtInIds.contains(id) {
            found.append(.builtInId)
        } else if !Self.isValidId(id) {
            found.append(.invalidId)
        } else if taken.contains(id) {
            found.append(.duplicateId)
        }
        if folder == nil {
            found.append(.noFolder)
        } else if !folderHasClaudeConfig {
            found.append(.folderNotClaude)
        }
        return found
    }

    public var request: AccountRequest? {
        guard let folder else { return nil }
        return AccountRequest(plugin: Self.plugin, id: id, label: label.trimmingCharacters(in: .whitespacesAndNewlines),
                              configDir: folder.path)
    }

    /// Whether `folder` holds Claude Code's `.claude.json` (what an account's CLAUDE_CONFIG_DIR contains).
    public static func folderHasClaudeConfig(_ folder: URL?, fileManager: FileManager = .default) -> Bool {
        guard let folder else { return false }
        return fileManager.fileExists(atPath: folder.appendingPathComponent(".claude.json").path)
    }
}

/// One `instance add` call. The monthly price is left unset; Settings sets it later.
public struct AccountRequest: Equatable, Sendable {
    public var plugin: String
    public var id: String
    public var label: String
    public var configDir: String

    public init(plugin: String, id: String, label: String, configDir: String) {
        self.plugin = plugin
        self.id = id
        self.label = label
        self.configDir = configDir
    }

    /// Arguments after the collector module's path.
    public func arguments(configPath: String) -> [String] {
        ["instance", "add", "--plugin", plugin, "--id", id, "--label", label, "--config-dir", configDir,
         "--config", configPath]
    }
}

/// What one add-account submission ended as, kept by that submission alone: the shared
/// action banner can be changed or dismissed by other actions while it finishes.
public enum AccountSaveResult: Equatable, Sendable {
    case saved
    case failed(String)
    /// Not started: another submission or action was still running.
    case busy
}

/// The add-account form's submission: busy for the submission's whole lifetime, and
/// closed only by a save. A failure keeps the form, and its draft, as they were.
public struct AccountSubmission: Equatable, Sendable {
    public private(set) var inFlight = false
    public private(set) var failure: String?

    public init(failure: String? = nil) { self.failure = failure }

    /// False while a submission is already in flight.
    public mutating func begin() -> Bool {
        guard !inFlight else { return false }
        inFlight = true
        failure = nil
        return true
    }

    /// True when the form should close.
    public mutating func finish(_ result: AccountSaveResult) -> Bool {
        inFlight = false
        switch result {
        case .saved: return true
        case let .failed(reason): failure = reason
        case .busy: failure = "Another change is still being saved. Try again when it finishes."
        }
        return false
    }
}
