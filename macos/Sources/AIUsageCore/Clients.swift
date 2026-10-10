import Foundation

public struct ProcessResult: Equatable, Sendable {
    public var exitCode: Int32
    public var stdout: Data
    public var stderr: String
    public var timedOut: Bool
}

public protocol ProcessRunning: Sendable {
    func run(_ executable: URL, arguments: [String], environment: [String: String], timeout: TimeInterval) async throws -> ProcessResult
    /// Like `run`, also delivering each complete stdout line as it arrives.
    func run(_ executable: URL, arguments: [String], environment: [String: String], timeout: TimeInterval,
             onLine: @escaping @Sendable (String) -> Void) async throws -> ProcessResult
}

public extension ProcessRunning {
    func run(_ executable: URL, arguments: [String], environment: [String: String], timeout: TimeInterval,
             onLine: @escaping @Sendable (String) -> Void) async throws -> ProcessResult {
        let result = try await run(executable, arguments: arguments, environment: environment, timeout: timeout)
        String(decoding: result.stdout, as: UTF8.self).split(separator: "\n").forEach { onLine(String($0)) }
        return result
    }
}

/// Runs a child process off the main thread, draining stdout and stderr
/// concurrently so large reports cannot deadlock on a full pipe.
public struct SystemProcessRunner: ProcessRunning {
    public init() {}

    public func run(_ executable: URL, arguments: [String], environment: [String: String], timeout: TimeInterval) async throws -> ProcessResult {
        try await run(executable, arguments: arguments, environment: environment, timeout: timeout, onLine: { _ in })
    }

    public func run(_ executable: URL, arguments: [String], environment: [String: String], timeout: TimeInterval,
                    onLine: @escaping @Sendable (String) -> Void) async throws -> ProcessResult {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                process.executableURL = executable
                process.arguments = arguments
                process.environment = environment
                let out = Pipe()
                let err = Pipe()
                process.standardOutput = out
                process.standardError = err
                process.standardInput = FileHandle.nullDevice
                do {
                    try process.run()
                } catch {
                    continuation.resume(throwing: error)
                    return
                }
                var stdout = Data()
                var stderr = Data()
                let group = DispatchGroup()
                group.enter()
                DispatchQueue.global().async {
                    // Read incrementally so progress lines arrive while the process runs.
                    var pending = Data()
                    let handle = out.fileHandleForReading
                    while true {
                        let chunk = handle.availableData
                        if chunk.isEmpty { break }
                        stdout.append(chunk)
                        pending.append(chunk)
                        while let newline = pending.firstIndex(of: 0x0A) {
                            onLine(String(decoding: pending[pending.startIndex..<newline], as: UTF8.self))
                            pending.removeSubrange(pending.startIndex...newline)
                        }
                    }
                    group.leave()
                }
                group.enter()
                DispatchQueue.global().async { stderr = err.fileHandleForReading.readDataToEndOfFile(); group.leave() }
                var timedOut = false
                let deadline = DispatchTime.now() + timeout
                let finished = DispatchSemaphore(value: 0)
                process.terminationHandler = { _ in finished.signal() }
                if finished.wait(timeout: deadline) == .timedOut {
                    timedOut = true
                    process.terminate()
                    finished.wait()
                }
                group.wait()
                continuation.resume(returning: ProcessResult(
                    exitCode: process.terminationStatus,
                    stdout: stdout,
                    stderr: String(decoding: stderr, as: UTF8.self),
                    timedOut: timedOut
                ))
            }
        }
    }
}

// MARK: - Report

public protocol ReportFetching: Sendable {
    func fetch() async throws -> Report
}

public enum ClientError: Error, Equatable, LocalizedError {
    case reportScriptMissing
    case collectorMissing
    case processFailed(String)
    case timedOut(String)

    public var errorDescription: String? {
        switch self {
        case .reportScriptMissing:
            return "The reporting script isn’t bundled with this app. Rebuild it with `make app-build`."
        case .collectorMissing:
            return "The collector isn’t installed. Run `python3 ai_usage_service.py install` from the repository."
        case let .processFailed(message):
            return message
        case let .timedOut(what):
            return "\(what) took too long and was stopped."
        }
    }
}

/// Runs `ai_usage_report.py` (read-only) and decodes its JSON.
public struct LiveReportClient: ReportFetching {
    public var environment: CollectorEnvironment
    public var runner: ProcessRunning

    public init(environment: CollectorEnvironment, runner: ProcessRunning = SystemProcessRunner()) {
        self.environment = environment
        self.runner = runner
    }

    public func fetch() async throws -> Report {
        guard let script = environment.reportScript else { throw ClientError.reportScriptMissing }
        var arguments = [script.path, "--config", environment.configPath.path]
        if environment.skipServiceProbe { arguments += ["--service", "skip"] }
        let result = try await runner.run(environment.python, arguments: arguments,
                                          environment: environment.childEnvironment, timeout: 120)
        if result.timedOut { throw ClientError.timedOut("Reading collector data") }
        guard result.exitCode == 0 else {
            let message = result.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            throw ClientError.processFailed(message.isEmpty ? "The report exited with status \(result.exitCode)." : message)
        }
        return try Report.decode(result.stdout)
    }
}

// MARK: - Collect now

public struct CollectRun: Equatable, Sendable {
    public enum Outcome: String, Sendable { case complete, partial, failed }
    public var outcome: Outcome
    public var message: String
    public var finishedAt: Date
}

public protocol CollectorRunning: Sendable {
    /// `progressSupported`: the installed collector accepts `once --progress` (2.2.0+).
    func collectOnce(progressSupported: Bool, onProgress: @escaping @Sendable (CollectProgress) -> Void) async throws -> CollectRun
}

/// Runs the installed collector's existing one-time path: `<python> collector.py once --config <cfg>`.
/// The collector serializes this with the scheduled daemon through its state-file flock.
/// It never starts or stops the LaunchAgent.
public struct LiveCollectorClient: CollectorRunning {
    public var environment: CollectorEnvironment
    public var runner: ProcessRunning
    public var now: @Sendable () -> Date

    public init(environment: CollectorEnvironment, runner: ProcessRunning = SystemProcessRunner(), now: @escaping @Sendable () -> Date = Date.init) {
        self.environment = environment
        self.runner = runner
        self.now = now
    }

    public func collectOnce(progressSupported: Bool, onProgress: @escaping @Sendable (CollectProgress) -> Void) async throws -> CollectRun {
        guard let script = environment.collectorScript, FileManager.default.fileExists(atPath: script.path) else {
            throw ClientError.collectorMissing
        }
        var arguments = [script.path, "once", "--config", environment.configPath.path]
        if progressSupported { arguments.append("--progress") }
        let result = try await runner.run(
            environment.python,
            arguments: arguments,
            environment: environment.childEnvironment,
            timeout: 15 * 60,
            onLine: { line in
                guard line.hasPrefix("{"), let data = line.data(using: .utf8),
                      let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      event["event"] as? String == "provider",
                      let provider = event["provider"] as? String,
                      let index = event["index"] as? Int, let total = event["total"] as? Int else { return }
                onProgress(CollectProgress(providerId: provider, index: index, total: total))
            }
        )
        let stdout = String(decoding: result.stdout, as: UTF8.self)
            .split(separator: "\n").filter { !$0.hasPrefix("{") }.joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let stderr = result.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
        if result.timedOut {
            return CollectRun(outcome: .failed, message: "The check took longer than 15 minutes and was stopped.", finishedAt: now())
        }
        switch result.exitCode {
        case 0: return CollectRun(outcome: .complete, message: stdout, finishedAt: now())
        case 2: return CollectRun(outcome: .partial, message: stdout, finishedAt: now())
        default:
            return CollectRun(outcome: .failed, message: stderr.isEmpty ? "The collector exited with status \(result.exitCode)." : stderr, finishedAt: now())
        }
    }
}
