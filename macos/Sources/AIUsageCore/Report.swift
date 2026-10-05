import Foundation

/// Swift mirror of the `ai-usage/report/v1` contract (docs/CLI.md).
/// The report is the single source of aggregation and freshness; these types
/// only carry it. `nil` always means "unknown or not reported", never zero.
public struct Report: Codable, Equatable, Sendable {
    public static let supportedSchema = "ai-usage/report/v1"

    public var schema: String
    public var generatedAt: Date
    public var timezone: String
    public var today: String
    public var collector: CollectorInfo
    public var thresholds: Thresholds
    public var service: Service
    public var schedule: Schedule
    public var collection: CollectionState
    public var providers: [Provider]

    public func provider(_ id: String) -> Provider? { providers.first { $0.id == id } }
}

public struct CollectorInfo: Codable, Equatable, Sendable {
    public var version: String
    public var configPath: String
    public var usageCsv: String
    public var logFile: String
    public var intervalSeconds: Int
    public var csvRows: Int
    /// VERSION of the collector the LaunchAgent runs (read from its file, never executed).
    public var installedVersion: String?
    public var capabilities: Capabilities?

    public struct Capabilities: Codable, Equatable, Sendable {
        public var pause: Bool
        public var progress: Bool
        public var serviceControl: Bool
        public var settings: Bool
    }
}

public struct Thresholds: Codable, Equatable, Sendable {
    public var staleAfterSeconds: Int
    public var grokBillingStaleAfterSeconds: Int
}

public struct Service: Codable, Equatable, Sendable {
    public enum State: String, Codable, Sendable { case running, loaded, stopped, notInstalled = "not_installed", unknown }
    public var state: State
    public var pid: Int?
    /// launchd's persistent disable flag: a disabled agent stays stopped at login.
    public var disabled: Bool?
    public var detail: String
}

public struct Schedule: Codable, Equatable, Sendable {
    public var state: String
    public var intervalMinutes: Int
    public var pauseSupported: Bool
    /// `poll_paused` is set in config.json (in effect only when the installed collector supports it).
    public var pauseRequested: Bool?
    public var nextScheduledAt: Date?

    public var isPaused: Bool { state == "paused" }
}

public struct CollectionState: Codable, Equatable, Sendable {
    public enum Result: String, Codable, Sendable { case success, partial, failed }
    public var lastAttemptAt: Date?
    public var lastAttemptResult: Result?
    public var lastSuccessAt: Date?
    public var failures: [Failure]
    public var summary: Summary?

    public struct Failure: Codable, Equatable, Sendable {
        public var provider: String?
        public var source: String?
        public var kind: String
        public var at: Date?
        public var message: String?
        public var auth: Bool
    }

    public struct Summary: Codable, Equatable, Sendable {
        public var providersAttempted: Int
        public var providersUpdated: Int
        public var providersPartly: Int
        public var providersFailed: Int
        public var skipped: [String]
    }
}

public struct Provider: Codable, Equatable, Identifiable, Sendable {
    public enum Setup: String, Codable, Sendable {
        case ready, notDetected = "not_detected", disabled, notConfigured = "not_configured", waiting
    }

    public var id: String
    public var name: String
    public var product: String
    public var enabled: Bool
    public var setup: Setup
    public var setupNote: String?
    public var usage: Usage
    public var coverageStart: String?
    public var today: Today?
    public var days: [Day]
    public var modelsToday: [Model]
    public var quota: Quota
    public var costs: Costs
    public var sources: [Source]
    public var notes: [String]

    public var isActive: Bool { enabled && setup == .ready }
    public var activeWindows: [QuotaWindow] { quota.windows.filter { !$0.retired } }
}

public enum Freshness: String, Codable, Sendable { case current, stale, none }

public struct Usage: Codable, Equatable, Sendable {
    public var status: Freshness
    public var staleCause: StaleCause?
    public var becomesStaleAt: Date?
    public var becomesStaleCause: StaleCause?
    public var measuredAt: Date?
    public var collectedAt: Date?
    public var recordKind: String
    public var mode: String
    public var split: String

    public var hasSplit: Bool { split == "input_output" }
}

public enum StaleCause: String, Codable, Sendable {
    case auth, failed, notCollected = "not_collected", sourceOld = "source_old", resetPassed = "reset_passed"
}

public struct Today: Codable, Equatable, Sendable {
    public var date: String
    public var input: Double?
    public var output: Double?
    public var total: Double?
    public var cacheRead: Double?
    public var cacheWrite: Double?
    public var asOf: Date?
    public var partial: Bool
}

public struct Day: Codable, Equatable, Identifiable, Sendable {
    public enum State: String, Codable, Sendable {
        case measured, zero, missing, notCollected = "not_collected", partial
    }

    public var date: String
    public var state: State
    public var input: Double?
    public var output: Double?
    public var total: Double?
    public var cacheRead: Double?
    public var cacheWrite: Double?
    public var incompleteEvents: Int
    /// For a partial day: when its source was last read (today so far, or a past day cut short).
    public var asOf: Date?

    public var id: String { date }
    public var hasValue: Bool { state == .measured || state == .zero || state == .partial }
}

public struct Model: Codable, Equatable, Sendable {
    public var name: String
    public var tokens: Double
}

public struct Quota: Codable, Equatable, Sendable {
    public enum Status: String, Codable, Sendable { case current, stale, mixed, unavailable, unsupported, none }
    public var status: Status
    public var staleCause: StaleCause?
    public var measuredAt: Date?
    public var collectedAt: Date?
    public var note: String?
    public var windows: [QuotaWindow]
}

public struct QuotaWindow: Codable, Equatable, Identifiable, Sendable {
    public enum Basis: String, Codable, Sendable { case remaining, used }
    public enum Limit: String, Codable, Sendable { case reached, low }

    public var id: String
    public var label: String
    public var short: String
    public var basis: Basis
    public var derived: Bool
    public var percent: Double?
    public var windowSeconds: Double?
    public var measuredAt: Date?
    public var collectedAt: Date?
    public var resetsAt: Date?
    public var resetPassed: Bool
    public var status: Freshness
    public var staleCause: StaleCause?
    public var becomesStaleAt: Date?
    public var becomesStaleCause: StaleCause?
    public var omitted: Bool
    public var retired: Bool
    public var limit: Limit?
    public var readings: [Reading]
    public var resets: [Date]
    public var ranges: [String]

    public struct Reading: Codable, Equatable, Sendable {
        public var t: Date
        public var percent: Double
        public var resetsAt: Date?
    }
}

public struct Costs: Codable, Equatable, Sendable {
    public var reported: Reported?
    public var reportedNote: String?
    public var apiEquivalent: APIEquivalent?
    public var subscription: Subscription?

    public struct Reported: Codable, Equatable, Sendable {
        public var amountUsd: Double
        public var basis: String
        public var periodStart: Date?
        public var periodEnd: Date?
        public var measuredAt: Date?
    }

    public struct APIEquivalent: Codable, Equatable, Sendable {
        public var amountUsd: Double
        public var basis: String
        public var rangeDays: Int
    }

    public struct Subscription: Codable, Equatable, Sendable {
        public var monthlyUsd: Double
        public var source: String
        public var enteredByUser: Bool
    }
}

public struct Source: Codable, Equatable, Identifiable, Sendable {
    public enum Status: String, Codable, Sendable {
        case ok, failed, stale, auth, off, waiting, notDetected = "not_detected"
    }

    public var id: String
    public var role: String
    public var label: String
    public var kind: String
    public var status: Status
    public var lastAttemptAt: Date?
    public var lastReadAt: Date?
    public var measuredAt: Date?
    public var error: String?

    public var hasProblem: Bool { status == .failed || status == .auth || status == .stale }
}

// MARK: - Decoding

public enum ReportError: Error, Equatable, LocalizedError {
    case unsupportedSchema(String)
    case malformed(String)

    public var errorDescription: String? {
        switch self {
        case let .unsupportedSchema(schema):
            return "The report format \(schema) isn’t supported by this app (expected \(Report.supportedSchema))."
        case let .malformed(detail):
            return "The report couldn’t be read: \(detail)"
        }
    }
}

public extension Report {
    static func decode(_ data: Data) throws -> Report {
        struct Header: Decodable { var schema: String }
        let decoder = JSONDecoder.report
        guard let header = try? decoder.decode(Header.self, from: data) else {
            throw ReportError.malformed("missing schema")
        }
        guard header.schema == supportedSchema else { throw ReportError.unsupportedSchema(header.schema) }
        do {
            return try decoder.decode(Report.self, from: data)
        } catch {
            throw ReportError.malformed(String(describing: error))
        }
    }

    func encoded() throws -> Data { try JSONEncoder.report.encode(self) }
}

extension JSONDecoder {
    static var report: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = ReportDate.parse(text) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "bad timestamp \(text)")
            }
            return date
        }
        return decoder
    }
}

extension JSONEncoder {
    static var report: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(ReportDate.format(date))
        }
        return encoder
    }
}

public enum ReportDate {
    private static let formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    public static func parse(_ text: String) -> Date? { formatter.date(from: text) }
    public static func format(_ date: Date) -> String { formatter.string(from: date) }

    /// Parses a local calendar date (`YYYY-MM-DD`) in the given time zone.
    public static func day(_ text: String, in zone: TimeZone = .current) -> Date? {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        let parts = text.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12))
    }
}

public extension Report {
    /// The committed fixture, generated by `make fixture` from synthetic data.
    /// For previews and tests only; the app never shows it as real data.
    static func fixture() throws -> Report {
        guard let url = Bundle.module.url(forResource: "report-fixture", withExtension: "json", subdirectory: "fixtures") else {
            throw ReportError.malformed("fixture resource missing")
        }
        return try decode(Data(contentsOf: url))
    }
}

// MARK: - Re-evaluation of a stored snapshot

public extension Report {
    /// The snapshot as it reads at `now`. Freshness rules live in the reporting
    /// layer; this only applies the deadlines it published (`becomes_stale_at`),
    /// so a cached or aging snapshot can never present stale readings as current.
    /// Measurements and their timestamps are never changed.
    func evaluated(at now: Date) -> Report {
        var copy = self.rolledOver(to: now)
        for index in copy.providers.indices {
            var provider = copy.providers[index]
            if provider.usage.status == .current, let deadline = provider.usage.becomesStaleAt, deadline <= now {
                provider.usage.status = .stale
                provider.usage.staleCause = provider.usage.becomesStaleCause ?? .notCollected
            }
            var changed = false
            for w in provider.quota.windows.indices where provider.quota.windows[w].status == .current {
                if let deadline = provider.quota.windows[w].becomesStaleAt, deadline <= now {
                    let cause = provider.quota.windows[w].becomesStaleCause ?? .notCollected
                    provider.quota.windows[w].status = .stale
                    provider.quota.windows[w].staleCause = cause
                    provider.quota.windows[w].limit = nil  // limits come from current windows only
                    if cause == .resetPassed { provider.quota.windows[w].resetPassed = true }
                    changed = true
                }
            }
            if changed {
                let active = provider.quota.windows.filter { !$0.retired && $0.status != .none }
                let statuses = Set(active.map(\.status))
                if statuses == [.stale] { provider.quota.status = .stale }
                else if statuses.count > 1 { provider.quota.status = .mixed }
                provider.quota.staleCause = active.first { $0.status == .stale }?.staleCause
            }
            copy.providers[index] = provider
        }
        return copy
    }
}

// MARK: - Calendar rollover

public extension Report {
    /// The local calendar date of `date` in the report's timezone.
    func localDay(_ date: Date) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// A snapshot carried past midnight (in its timezone) no longer describes
    /// "today": today's totals and models are dropped, the old day stays a
    /// partial day with its as-of time, and the daily window slides onto the
    /// new date with missing (not zero) days until the report is re-read.
    func rolledOver(to now: Date) -> Report {
        let newToday = localDay(now)
        guard newToday > today else { return self }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        var added: [String] = []
        var cursor = ReportDate.day(today, in: calendar.timeZone)
        while let current = cursor, added.count < 400 {
            guard let next = calendar.date(byAdding: .day, value: 1, to: current) else { break }
            let label = localDay(next)
            if label > newToday { break }
            added.append(label)
            cursor = next
        }
        var copy = self
        copy.today = newToday
        for index in copy.providers.indices {
            var provider = copy.providers[index]
            let asOf = provider.today?.asOf
            for d in provider.days.indices where provider.days[d].date == today && provider.days[d].state == .partial {
                provider.days[d].asOf = provider.days[d].asOf ?? asOf
            }
            provider.today = nil
            provider.modelsToday = []
            let count = provider.days.count
            // A provider that has never collected stays "not collected", as a fresh report would say.
            let newState: Day.State = provider.coverageStart == nil ? .notCollected : .missing
            provider.days += added.map {
                Day(date: $0, state: newState, input: nil, output: nil, total: nil, cacheRead: nil, cacheWrite: nil,
                    incompleteEvents: 0, asOf: nil)
            }
            provider.days = Array(provider.days.suffix(count))
            copy.providers[index] = provider
        }
        return copy
    }
}
