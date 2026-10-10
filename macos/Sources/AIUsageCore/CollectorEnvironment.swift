import Foundation

/// Where the collector lives and how to run it, resolved without relying on the
/// GUI app's minimal PATH. Order: explicit environment overrides, then the
/// LaunchAgent plist's ProgramArguments/EnvironmentVariables, then defaults.
public struct CollectorEnvironment: Equatable, Sendable {
    public static let serviceLabel = "codes.porta.ai-usage"

    public var python: URL
    public var collectorScript: URL?
    public var configPath: URL
    public var reportScript: URL?
    public var skipServiceProbe: Bool
    public var stateDirectory: URL
    public var childEnvironment: [String: String]
    /// Human-readable notes on how each value was resolved (shown in diagnostics).
    public var provenance: [String]

    public init(
        python: URL, collectorScript: URL?, configPath: URL, reportScript: URL?,
        skipServiceProbe: Bool, stateDirectory: URL, childEnvironment: [String: String], provenance: [String] = []
    ) {
        self.python = python
        self.collectorScript = collectorScript
        self.configPath = configPath
        self.reportScript = reportScript
        self.skipServiceProbe = skipServiceProbe
        self.stateDirectory = stateDirectory
        self.childEnvironment = childEnvironment
        self.provenance = provenance
    }

    public static func resolve(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        home: URL = FileManager.default.homeDirectoryForCurrentUser,
        bundleResources: URL? = Bundle.main.resourceURL,
        fileManager: FileManager = .default
    ) -> CollectorEnvironment {
        var notes: [String] = []
        let plistURL = home.appendingPathComponent("Library/LaunchAgents/\(serviceLabel).plist")
        var programArguments: [String] = []
        var plistEnvironment: [String: String] = [:]
        if let data = try? Data(contentsOf: plistURL),
           let plist = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] {
            programArguments = plist["ProgramArguments"] as? [String] ?? []
            plistEnvironment = plist["EnvironmentVariables"] as? [String: String] ?? [:]
            notes.append("LaunchAgent plist: \(plistURL.path)")
        } else {
            notes.append("LaunchAgent plist not found at \(plistURL.path)")
        }

        func override(_ key: String) -> String? {
            guard let value = environment[key], !value.isEmpty else { return nil }
            notes.append("\(key)=\(value)")
            return value
        }

        let python = override("AI_USAGE_PYTHON")
            ?? programArguments.first
            ?? "/usr/bin/python3"
        var collector = override("AI_USAGE_COLLECTOR")
        if collector == nil, programArguments.count > 1 { collector = programArguments[1] }

        var config = override("AI_USAGE_CONFIG")
        if config == nil, let index = programArguments.firstIndex(of: "--config"), index + 1 < programArguments.count {
            config = programArguments[index + 1]
        }
        let configURL = URL(fileURLWithPath: config ?? home.appendingPathComponent(".ai-usage/config.json").path)

        var report = override("AI_USAGE_REPORT_SCRIPT").map { URL(fileURLWithPath: $0) }
        if report == nil, let resources = bundleResources {
            let bundled = resources.appendingPathComponent("collector/ai_usage_report.py")
            if fileManager.fileExists(atPath: bundled.path) { report = bundled }
        }

        let stateDir = override("AI_USAGE_STATE_DIR").map { URL(fileURLWithPath: $0) }
            ?? home.appendingPathComponent("Library/Application Support/AI Usage")

        var child = plistEnvironment
        child["HOME"] = home.path
        if child["PATH"] == nil { child["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin" }
        // Never write bytecode into the signed app bundle.
        child["PYTHONDONTWRITEBYTECODE"] = "1"
        if let tz = environment["TZ"] { child["TZ"] = tz }

        return CollectorEnvironment(
            python: URL(fileURLWithPath: python),
            collectorScript: collector.map { URL(fileURLWithPath: $0) },
            configPath: configURL,
            reportScript: report,
            skipServiceProbe: override("AI_USAGE_SERVICE") == "skip",
            stateDirectory: stateDir,
            childEnvironment: child,
            provenance: notes
        )
    }
}
