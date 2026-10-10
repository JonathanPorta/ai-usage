import Foundation

// MARK: - Collect now progress

public struct CollectProgress: Equatable, Sendable {
    public var providerId: String
    public var index: Int
    public var total: Int
}

// MARK: - Service control

public struct ServiceStatus: Equatable, Sendable, Decodable {
    public var installed: Bool
    public var state: Service.State
    public var pid: Int?
    public var disabled: Bool?
    public var detail: String

    public var asReportService: Service { Service(state: state, pid: pid, disabled: disabled, detail: detail) }
}

public protocol ServiceControlling: Sendable {
    func status() async throws -> ServiceStatus
    func start() async throws
    func stop() async throws
}

public struct NoServiceControl: ServiceControlling {
    public init() {}
    public func status() async throws -> ServiceStatus { throw ClientError.processFailed("Service control isn’t available here.") }
    public func start() async throws { throw ClientError.processFailed("Service control isn’t available here.") }
    public func stop() async throws { throw ClientError.processFailed("Service control isn’t available here.") }
}

/// Runs the bundled collector module's `service-status`, `start` and `stop`.
/// Stop disables the LaunchAgent and boots it out (stays stopped at login);
/// Start re-enables and bootstraps it. Installation, config and data are untouched.
public struct LiveServiceControl: ServiceControlling {
    public var environment: CollectorEnvironment
    public var runner: ProcessRunning

    public init(environment: CollectorEnvironment, runner: ProcessRunning = SystemProcessRunner()) {
        self.environment = environment
        self.runner = runner
    }

    public func status() async throws -> ServiceStatus { try await run("service-status", timeout: 15) }
    public func start() async throws { _ = try await run("start", timeout: 60) }
    public func stop() async throws { _ = try await run("stop", timeout: 60) }

    private func run(_ command: String, timeout: TimeInterval) async throws -> ServiceStatus {
        // Defense in depth: an isolated (sandbox) environment never controls or probes launchd.
        guard !environment.skipServiceProbe else {
            throw ClientError.processFailed("Service control is disabled for this isolated (sandbox) data source.")
        }
        guard let module = environment.bundledCollectorModule else { throw ClientError.reportScriptMissing }
        let result = try await runner.run(environment.python, arguments: [module.path, command],
                                          environment: environment.childEnvironment, timeout: timeout)
        if result.timedOut { throw ClientError.timedOut("launchctl") }
        guard result.exitCode == 0 else {
            let message = result.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            throw ClientError.processFailed(message.isEmpty ? "\(command) exited with status \(result.exitCode)." : message)
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(ServiceStatus.self, from: result.stdout)
        } catch {
            throw ClientError.processFailed("Unexpected service status: \(String(decoding: result.stdout, as: UTF8.self))")
        }
    }
}

// MARK: - Settings

public enum ConfigChange: Equatable, Sendable {
    case pollPaused(Bool)
    case pollIntervalSeconds(Int)
    case providerEnabled(String, Bool)
    case monthlySubscription(String, Double?)

    /// `KEY=JSON` for `ai_usage_service.py configure --set`.
    public var assignment: String {
        switch self {
        case let .pollPaused(value): return "poll_paused=\(value)"
        case let .pollIntervalSeconds(value): return "poll_interval_seconds=\(value)"
        case let .providerEnabled(id, value): return "providers.\(id).enabled=\(value)"
        case let .monthlySubscription(id, value):
            guard let value else { return "providers.\(id).monthly_subscription_usd=null" }
            return "providers.\(id).monthly_subscription_usd=\(value == value.rounded() ? String(Int(value)) : String(value))"
        }
    }
}

public protocol ConfigWriting: Sendable {
    func apply(_ changes: [ConfigChange]) async throws
}

public struct NoConfigWriter: ConfigWriting {
    public init() {}
    public func apply(_ changes: [ConfigChange]) async throws { throw ClientError.processFailed("Settings can’t be saved here.") }
}

/// Validated, atomic config updates through the bundled module's `configure`
/// (locked, merged-config validation, temp file + rename, mode kept, other keys preserved).
public struct LiveConfigWriter: ConfigWriting {
    public var environment: CollectorEnvironment
    public var runner: ProcessRunning

    public init(environment: CollectorEnvironment, runner: ProcessRunning = SystemProcessRunner()) {
        self.environment = environment
        self.runner = runner
    }

    public func apply(_ changes: [ConfigChange]) async throws {
        guard let module = environment.bundledCollectorModule else { throw ClientError.reportScriptMissing }
        var arguments = [module.path, "configure", "--config", environment.configPath.path]
        for change in changes { arguments += ["--set", change.assignment] }
        let result = try await runner.run(environment.python, arguments: arguments,
                                          environment: environment.childEnvironment, timeout: 30)
        if result.timedOut { throw ClientError.timedOut("Saving settings") }
        guard result.exitCode == 0 else {
            let message = result.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
                .replacingOccurrences(of: "ai-usage: ", with: "")
            throw ClientError.processFailed(message.isEmpty ? "Settings weren’t saved." : message)
        }
    }
}

extension CollectorEnvironment {
    /// The collector module bundled beside the report script (same build as the app).
    public var bundledCollectorModule: URL? {
        reportScript?.deletingLastPathComponent().appendingPathComponent("ai_usage_service.py")
    }
}
