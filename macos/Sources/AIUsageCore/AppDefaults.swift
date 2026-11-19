import Foundation

/// The one UserDefaults store the app reads and writes. A sandbox run
/// (AI_USAGE_STATE_DIR set) shares the installed app's bundle id, so it must not
/// use `.standard`: its preferences go to `<state dir>/preferences.plist`
/// instead, and the owner's real preferences are never touched.
public enum AppDefaults {
    /// The overridden state directory, or nil for a normal run.
    public static func sandboxStateDirectory(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> URL? {
        guard let value = environment["AI_USAGE_STATE_DIR"], !value.isEmpty else { return nil }
        return URL(fileURLWithPath: value)
    }

    /// `.standard` normally; a plist-backed suite under the sandbox state dir otherwise.
    public static func store(environment: [String: String] = ProcessInfo.processInfo.environment) -> UserDefaults {
        guard let directory = sandboxStateDirectory(environment: environment) else { return .standard }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        // An absolute-path suite name is stored at that path plus ".plist".
        return UserDefaults(suiteName: directory.appendingPathComponent("preferences").path) ?? .standard
    }

    /// Whether this process is a sandbox run (shown as a "Sandbox" marker in the UI).
    public static let isSandbox = sandboxStateDirectory() != nil

    /// The process-wide store.
    public static let shared: UserDefaults = store()
}
