import Foundation

// File-defined themes (docs/THEMES.md).
//
// The owner keeps named color schemes in `<state directory>/themes.json` and applies
// one globally and others to single provider instances. Resolution, per appearance and
// per role: built-in token -> global scheme -> provider-instance scheme. Everything here
// is pure data: the file is read locally, size-capped and parsed as JSON; nothing in it
// is executed, fetched or interpreted beyond color values and names.

/// One role's colors in a scheme. A missing appearance inherits the layer below.
public struct ThemeColor: Equatable, Sendable {
    public var light: UInt32?
    public var dark: UInt32?

    public init(light: UInt32? = nil, dark: UInt32? = nil) {
        self.light = light
        self.dark = dark
    }

    /// `self` over `base`: each appearance set here wins.
    public func over(_ base: ThemeColor?) -> ThemeColor {
        ThemeColor(light: light ?? base?.light, dark: dark ?? base?.dark)
    }
}

public struct ThemeScheme: Equatable, Sendable {
    /// Role name -> colors. Roles are the semantic token names (`ThemeRoles.all`).
    public var roles: [String: ThemeColor]

    public init(roles: [String: ThemeColor] = [:]) { self.roles = roles }
}

/// A validated theme file.
public struct ThemeDocument: Equatable, Sendable {
    public static let supportedVersion = 1

    public var global: String?
    /// Provider instance id -> scheme name. Ids that are not in the current report are
    /// kept, so the assignment applies again when that instance returns.
    public var providers: [String: String]
    public var schemes: [String: ThemeScheme]

    public init(global: String? = nil, providers: [String: String] = [:], schemes: [String: ThemeScheme] = [:]) {
        self.global = global
        self.providers = providers
        self.schemes = schemes
    }

    /// The overrides for one scope: the global scheme, then the instance's own scheme
    /// on top. `nil` instance = shared chrome (global only). Roles absent from both are
    /// absent here and fall back to the built-in tokens.
    public func roles(for instance: String?) -> [String: ThemeColor] {
        var out: [String: ThemeColor] = [:]
        if let global, let scheme = schemes[global] { out = scheme.roles }
        if let instance, let name = providers[instance], let scheme = schemes[name] {
            for (role, color) in scheme.roles { out[role] = color.over(out[role]) }
        }
        return out
    }
}

/// A theme file problem: what is wrong and where (a JSON path).
public struct ThemeError: Error, Equatable, Sendable, CustomStringConvertible {
    public var path: String
    public var message: String

    public init(_ path: String, _ message: String) {
        self.path = path
        self.message = message
    }

    public var description: String { path.isEmpty ? message : "\(path): \(message)" }
}

public enum ThemeParser {
    /// Larger files are refused unread; a real theme file is a few kilobytes.
    public static let maxBytes = 64 * 1024

    /// Keys starting with `$` or `_` are notes ("_comment") and are ignored everywhere.
    static func isNote(_ key: String) -> Bool { key.hasPrefix("$") || key.hasPrefix("_") }

    public static func parse(_ data: Data) -> Result<ThemeDocument, ThemeError> {
        if data.count > maxBytes {
            return .failure(ThemeError("", "file is \(data.count / 1024) KB; the limit is \(maxBytes / 1024) KB"))
        }
        let object: Any
        do {
            object = try JSONSerialization.jsonObject(with: data)
        } catch {
            let detail = (error as NSError).userInfo[NSDebugDescriptionErrorKey] as? String
            return .failure(ThemeError("", "not valid JSON" + (detail.map { " (\($0))" } ?? "")))
        }
        do {
            return .success(try document(object))
        } catch let error as ThemeError {
            return .failure(error)
        } catch {
            return .failure(ThemeError("", "\(error)"))
        }
    }

    private static func document(_ object: Any) throws -> ThemeDocument {
        guard let root = object as? [String: Any] else { throw ThemeError("", "the top level must be an object") }
        for key in root.keys where !isNote(key) && !["version", "global", "providers", "schemes"].contains(key) {
            throw ThemeError(key, "unknown key (expected version, global, providers, schemes)")
        }
        guard let version = root["version"] as? NSNumber, CFGetTypeID(version) == CFNumberGetTypeID(),
              version.doubleValue == Double(ThemeDocument.supportedVersion) else {
            throw ThemeError("version", "must be \(ThemeDocument.supportedVersion)")
        }
        var schemes: [String: ThemeScheme] = [:]
        if let raw = root["schemes"] {
            guard let table = raw as? [String: Any] else { throw ThemeError("schemes", "must be an object of named schemes") }
            for name in table.keys.sorted() where !isNote(name) {
                schemes[name] = try scheme(table[name]!, path: "schemes.\(name)")
            }
        }
        func reference(_ value: Any?, path: String) throws -> String? {
            guard let value, !(value is NSNull) else { return nil }
            guard let name = value as? String else { throw ThemeError(path, "must be a scheme name") }
            guard schemes[name] != nil else {
                let known = schemes.keys.sorted().map { "\"\($0)\"" }.joined(separator: ", ")
                throw ThemeError(path, "scheme \"\(name)\" is not defined in schemes" + (known.isEmpty ? "" : " (defined: \(known))"))
            }
            return name
        }
        let global = try reference(root["global"], path: "global")
        var providers: [String: String] = [:]
        if let raw = root["providers"] {
            guard let table = raw as? [String: Any] else { throw ThemeError("providers", "must map provider ids to scheme names") }
            for id in table.keys.sorted() where !isNote(id) {
                if let name = try reference(table[id], path: "providers.\(id)") { providers[id] = name }
            }
        }
        return ThemeDocument(global: global, providers: providers, schemes: schemes)
    }

    private static func scheme(_ object: Any, path: String) throws -> ThemeScheme {
        guard let table = object as? [String: Any] else { throw ThemeError(path, "must be an object with light and/or dark") }
        for key in table.keys where !isNote(key) && key != "light" && key != "dark" {
            throw ThemeError("\(path).\(key)", "unknown key (expected light, dark)")
        }
        let light = try appearance(table["light"], path: "\(path).light")
        let dark = try appearance(table["dark"], path: "\(path).dark")
        var roles: [String: ThemeColor] = [:]
        for (role, value) in light { roles[role, default: ThemeColor()].light = value }
        for (role, value) in dark { roles[role, default: ThemeColor()].dark = value }
        return ThemeScheme(roles: roles)
    }

    private static func appearance(_ object: Any?, path: String) throws -> [String: UInt32] {
        guard let object else { return [:] }
        guard let table = object as? [String: Any] else { throw ThemeError(path, "must map roles to \"#RRGGBB\" colors") }
        var out: [String: UInt32] = [:]
        for key in table.keys.sorted() where !isNote(key) {
            let role = aliases[key] ?? key
            guard ThemeRoles.all.contains(role) else {
                throw ThemeError("\(path).\(key)", "unknown role; use a role listed in docs/THEMES.md")
            }
            guard let text = table[key] as? String, let value = color(text) else {
                throw ThemeError("\(path).\(key)", "\(describe(table[key]!)) is not a \"#RRGGBB\" color")
            }
            out[role] = value
        }
        return out
    }

    /// Accepted alternative role names: `surface` is `panel` (cards and panels).
    public static let aliases = ["surface": "panel"]

    /// `#RRGGBB`, case-insensitive. Nothing else (no names, alpha or short forms).
    public static func color(_ text: String) -> UInt32? {
        guard text.count == 7, text.first == "#" else { return nil }
        let digits = text.dropFirst()
        guard digits.allSatisfy(\.isHexDigit) else { return nil }
        return UInt32(digits, radix: 16)
    }

    private static func describe(_ value: Any) -> String {
        if let text = value as? String { return "\"\(text)\"" }
        return "\(value)"
    }
}

/// The theme the app applies, plus what Settings shows about it.
public struct ThemeState: Equatable, Sendable {
    /// The last valid document; `nil` = built-in colors only.
    public var document: ThemeDocument?
    /// One concise, actionable line when the file could not be applied as written.
    public var diagnostic: String?
    public var fileExists: Bool

    public init(document: ThemeDocument? = nil, diagnostic: String? = nil, fileExists: Bool = false) {
        self.document = document
        self.diagnostic = diagnostic
        self.fileExists = fileExists
    }

    public static let builtIn = ThemeState()

    public func roles(for instance: String?) -> [String: ThemeColor] { document?.roles(for: instance) ?? [:] }
    public var globalScheme: String? { document?.global }
    public var assignments: [String: String] { document?.providers ?? [:] }
}

/// Loads `themes.json` and keeps the last valid theme across bad edits.
public enum ThemeLoader {
    public static let fileName = "themes.json"

    public static func load(_ url: URL, previous: ThemeState = .builtIn) -> ThemeState {
        let manager = FileManager.default
        // A deliberate removal returns to built-in colors (it is not an invalid edit).
        guard manager.fileExists(atPath: url.path) else { return ThemeState() }
        let fallback = previous.document == nil ? "using built-in colors" : "keeping the last valid theme"
        func failed(_ message: String) -> ThemeState {
            ThemeState(document: previous.document,
                       diagnostic: "\(fileName): \(message). Fix the file and choose Reload themes; \(fallback).",
                       fileExists: true)
        }
        let data: Data
        switch readBounded(url) {
        case let .success(read): data = read
        case let .failure(error): return failed(error.description)
        }
        switch ThemeParser.parse(data) {
        case let .success(document): return ThemeState(document: document, fileExists: true)
        case let .failure(error): return failed(error.description)
        }
    }

    /// Reads a theme file only after checking it: a regular file (a symlink is followed only
    /// to one), at most `ThemeParser.maxBytes`. A FIFO, device or oversized file is never read.
    public static func readBounded(_ url: URL) -> Result<Data, ThemeError> {
        let target = url.resolvingSymlinksInPath()
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: target.path),
              attributes[.type] as? FileAttributeType == .typeRegular else {
            return .failure(ThemeError("", "is not a regular file"))
        }
        if let size = attributes[.size] as? NSNumber, size.intValue > ThemeParser.maxBytes {
            return .failure(ThemeError("", "file is \(size.intValue / 1024) KB; the limit is \(ThemeParser.maxBytes / 1024) KB"))
        }
        guard let data = try? Data(contentsOf: target) else { return .failure(ThemeError("", "could not be read")) }
        return .success(data)
    }

    /// The file "Open theme file" creates when none exists: valid, inert (no scheme is
    /// applied until the owner sets `global` or a provider), and an example of each part.
    public static let starter = """
    {
      "_comment": "AI Usage themes. Set \\"global\\" and/or \\"providers\\" to a scheme name, save, then choose Reload themes in Settings. Roles and rules: docs/THEMES.md. Colors are #RRGGBB; a role you leave out keeps the built-in color.",
      "version": 1,
      "global": null,
      "providers": {
        "_example": "claude-az: \\"violet\\""
      },
      "schemes": {
        "slate": {
          "light": { "accent": "#1769AA", "background": "#F3F5F7" },
          "dark": { "accent": "#80B7F7", "background": "#25272B" }
        },
        "warm": {
          "light": { "accent": "#9A4A24", "series1": "#9A4A24" },
          "dark": { "accent": "#EAAF86", "series1": "#EAAF86" }
        },
        "violet": {
          "light": { "accent": "#7245B8", "accentSoft": "#8F6BCB", "accentBackground": "#F1EBFA" },
          "dark": { "accent": "#B39AEF", "accentSoft": "#9479D6", "accentBackground": "#342A4A" }
        }
      }
    }

    """

    /// Writes the starter file unless a file is already there. Never overwrites.
    @discardableResult
    public static func ensureFile(_ url: URL) throws -> Bool {
        if FileManager.default.fileExists(atPath: url.path) { return false }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(starter.utf8).write(to: url, options: .withoutOverwriting)
        return true
    }
}

/// Owns one theme file location; `reload()` re-reads it, keeping the last valid theme.
/// After each successful load, a copy of the file is kept beside it
/// (`themes.last-valid.json`, owner-only), so a restart with an invalid file still shows
/// the last valid theme.
public final class ThemeStore {
    public static let lastValidName = "themes.last-valid.json"
    public let url: URL
    public var lastValidURL: URL { url.deletingLastPathComponent().appendingPathComponent(Self.lastValidName) }
    public private(set) var state: ThemeState

    public init(url: URL) {
        self.url = url
        state = ThemeState()
        var previous = ThemeState.builtIn
        if case let .success(data) = ThemeLoader.readBounded(lastValidURL), data.count <= ThemeParser.maxBytes,
           case let .success(document) = ThemeParser.parse(data) {
            previous = ThemeState(document: document)
        }
        state = load(previous: previous)
    }

    private func load(previous: ThemeState) -> ThemeState {
        let next = ThemeLoader.load(url, previous: previous)
        if next.fileExists, next.diagnostic == nil, let data = try? Data(contentsOf: url.resolvingSymlinksInPath()) {
            saveLastValid(data)
        }
        return next
    }

    private func saveLastValid(_ data: Data) {
        let manager = FileManager.default
        if (try? Data(contentsOf: lastValidURL)) == data { return }
        let temp = url.deletingLastPathComponent().appendingPathComponent(".\(Self.lastValidName).\(UUID().uuidString)")
        guard manager.createFile(atPath: temp.path, contents: data, attributes: [.posixPermissions: 0o600]) else { return }
        if (try? manager.replaceItemAt(lastValidURL, withItemAt: temp)) == nil {
            try? manager.removeItem(at: temp)
        }
    }

    public convenience init(directory: URL) {
        self.init(url: directory.appendingPathComponent(ThemeLoader.fileName))
    }

    @discardableResult
    public func reload() -> ThemeState {
        state = load(previous: state)
        return state
    }
}

/// Legibility check on text roles. Colors are applied as written; a very low ratio adds
/// a diagnostic line so the owner knows why something is hard to read.
public enum ThemeContrast {
    public static let minimum = 3.0

    /// `defaults`: role -> built-in (light, dark), for roles a scheme leaves out.
    public static func warnings(_ state: ThemeState, defaults: [String: (light: UInt32, dark: UInt32)]) -> [String] {
        guard let document = state.document else { return [] }
        var targets: [(label: String, instance: String?)] = []
        if let global = document.global { targets.append(("global scheme \"\(global)\"", nil)) }
        for (id, name) in document.providers.sorted(by: { $0.key < $1.key }) {
            targets.append(("scheme \"\(name)\" on \(id)", id))
        }
        var out: [String] = []
        for target in targets {
            let roles = document.roles(for: target.instance)
            for dark in [false, true] {
                func value(_ role: String) -> UInt32? {
                    let own = dark ? roles[role]?.dark : roles[role]?.light
                    return own ?? defaults[role].map { dark ? $0.dark : $0.light }
                }
                func touched(_ names: String...) -> Bool { names.contains { roles[$0] != nil } }
                var problems: [String] = []
                for (fg, bg) in pairs where touched(fg, bg) {
                    guard let a = value(fg), let b = value(bg) else { continue }
                    let r = ratio(a, b)
                    if r < minimum { problems.append(String(format: "%@ on %@ %.1f:1", fg, bg, r)) }
                }
                let status = ["success", "warning", "danger"]
                for (i, a) in status.enumerated() {
                    for b in status[(i + 1)...] where touched(a, b) {
                        guard let x = value(a), let y = value(b) else { continue }
                        if distance(x, y) < similar { problems.append("\(a) and \(b) look alike") }
                    }
                }
                if !problems.isEmpty {
                    out.append("Low contrast in \(target.label) (\(dark ? "dark" : "light")): "
                               + problems.joined(separator: ", ") + "; applied as written.")
                }
            }
        }
        return out
    }

    /// Foreground/background pairs that must stay legible (checked only where a scheme
    /// sets either role; the built-in pairs are known good).
    static let pairs: [(String, String)] = {
        var out = [("text", "background"), ("text", "panel"), ("muted", "panel")]
        for fg in ["success", "warning", "danger", "focus", "accent"] {
            out += [(fg, "panel"), (fg, "background")]
        }
        out += [("text", "accentBackground"), ("text", "warningBackground"), ("text", "dangerBackground")]
        return out
    }()

    /// Status hues closer than this (sRGB distance, 0-441) read as the same state.
    static let similar = 48.0

    static func distance(_ a: UInt32, _ b: UInt32) -> Double {
        func c(_ v: UInt32, _ shift: UInt32) -> Double { Double((v >> shift) & 0xFF) }
        let d = [16, 8, 0].map { c(a, UInt32($0)) - c(b, UInt32($0)) }
        return (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).squareRoot()
    }

    /// WCAG 2 contrast ratio between two sRGB colors.
    public static func ratio(_ a: UInt32, _ b: UInt32) -> Double {
        let la = luminance(a), lb = luminance(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }

    static func luminance(_ rgb: UInt32) -> Double {
        func channel(_ shift: UInt32) -> Double {
            let c = Double((rgb >> shift) & 0xFF) / 255
            return c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channel(16) + 0.7152 * channel(8) + 0.0722 * channel(0)
    }
}
