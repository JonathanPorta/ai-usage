import Foundation

// MARK: - Tracking policy (report fields)

/// The configured tracking policy of a provider instance.
public enum TrackingMode: String, Codable, Sendable {
    case auto, on, off
    /// A value from a newer collector; treated as absent.
    case unknown

    public init(from decoder: Decoder) throws {
        self = Self(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .unknown
    }

    public static let allCases: [TrackingMode] = [.auto, .on, .off]

    /// Label for the provider detail control.
    public var label: String {
        switch self {
        case .auto: return "Automatic"
        case .on: return "On"
        case .off: return "Off"
        case .unknown: return "Unknown"
        }
    }
}

/// What the collector actually does with an instance this run.
public enum TrackingEffective: String, Codable, Sendable {
    case active, inactive, pending
    /// A value from a newer collector; treated as absent.
    case unknown

    public init(from decoder: Decoder) throws {
        self = Self(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .unknown
    }
}

/// Install evidence, kept separate from saved local data.
public struct Detection: Codable, Equatable, Sendable {
    public enum Requirement: String, Codable, Sendable {
        case executable, none, unknown

        public init(from decoder: Decoder) throws {
            self = Self(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .unknown
        }
    }
    /// `nil`: unknown, failed probe, or no install requirement.
    public var installed: Bool?
    public var requirement: Requirement?
    public var savedData: Bool?

    public init(installed: Bool?, requirement: Requirement?, savedData: Bool?) {
        self.installed = installed
        self.requirement = requirement
        self.savedData = savedData
    }
}

public extension Provider {
    /// The configured policy; older reports fall back to `enabled` (true -> on, false -> off).
    var trackingMode: TrackingMode {
        if let tracking, tracking != .unknown { return tracking }
        return enabled ? .on : .off
    }

    /// The effective state; older reports fall back to `enabled`.
    var effectiveTracking: TrackingEffective {
        if let trackingEffective, trackingEffective != .unknown { return trackingEffective }
        return enabled ? .active : .inactive
    }

    /// Shown on the overview (cards and keyboard navigation). Explicit On is always visible.
    var isOverviewVisible: Bool { trackingMode == .on || (trackingMode != .off && effectiveTracking == .active) }
}

// MARK: - Detection notice

public struct DetectionNotice: Equatable, Identifiable, Sendable {
    public enum Action: String, Equatable, Sendable { case keepHistory, reviewSettings

        public var label: String {
            switch self {
            case .keepHistory: return "Keep history"
            case .reviewSettings: return "Review provider settings"
            }
        }
    }

    public static let message = "Saved usage found. We couldn't find this CLI."

    public var providerId: String
    public var providerName: String
    /// The condition this notice describes; acknowledgement is keyed by id + signature.
    public var signature: String
    public var title: String { Self.message }
    public var actions: [Action] { [.keepHistory, .reviewSettings] }
    public var defaultAction: Action { .keepHistory }
    public var id: String { providerId }
    public var acknowledgementKey: String { "\(providerId)#\(signature)" }

    /// The signature when `provider` meets the notice condition, otherwise nil.
    public static func signature(_ provider: Provider) -> String? {
        guard provider.tracking == .auto, let detection = provider.detection,
              detection.installed == false, detection.savedData == true else { return nil }
        return "auto|installed=false|saved=true"
    }

    /// One notice per provider meeting the condition and not yet acknowledged.
    public static func notices(_ report: Report, acknowledged: Set<String>) -> [DetectionNotice] {
        var seen = Set<String>()
        return report.providers.compactMap { provider in
            guard let signature = signature(provider), seen.insert(provider.id).inserted else { return nil }
            let notice = DetectionNotice(providerId: provider.id, providerName: provider.name, signature: signature)
            return acknowledged.contains(notice.acknowledgementKey) ? nil : notice
        }
    }

    /// Drops an acknowledgement only when the report positively shows the condition
    /// resolved (the CLI was found) or the provider is gone. Unknown probes, missing
    /// detection data and momentary non-matches keep it.
    public static func pruned(_ acknowledged: Set<String>, for report: Report) -> Set<String> {
        let byId = Dictionary(report.providers.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        return acknowledged.filter { key in
            let id = String(key[..<(key.lastIndex(of: "#") ?? key.endIndex)])
            guard let provider = byId[id] else { return false }
            return provider.detection?.installed != true
        }
    }
}

// MARK: - Preferences

/// Small key-value store for app preferences, injectable for tests.
public protocol PreferencesStoring: AnyObject, Sendable {
    func string(forKey key: String) -> String?
    func strings(forKey key: String) -> [String]?
    func set(_ value: String?, forKey key: String)
    func set(_ value: [String]?, forKey key: String)
}

public final class InMemoryPreferences: PreferencesStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: Any] = [:]

    public init() {}

    public func string(forKey key: String) -> String? { lock.withLock { values[key] as? String } }
    public func strings(forKey key: String) -> [String]? { lock.withLock { values[key] as? [String] } }
    public func set(_ value: String?, forKey key: String) { lock.withLock { values[key] = value } }
    public func set(_ value: [String]?, forKey key: String) { lock.withLock { values[key] = value } }
}

public final class UserDefaultsPreferences: PreferencesStoring, @unchecked Sendable {
    private let defaults: UserDefaults

    public init(_ defaults: UserDefaults = .standard) { self.defaults = defaults }

    public func string(forKey key: String) -> String? { defaults.string(forKey: key) }
    public func strings(forKey key: String) -> [String]? { defaults.stringArray(forKey: key) }
    public func set(_ value: String?, forKey key: String) { defaults.set(value, forKey: key) }
    public func set(_ value: [String]?, forKey key: String) { defaults.set(value, forKey: key) }
}

public enum PreferenceKey {
    public static let cardOrderMode = "cardOrderMode"
    public static let customCardOrder = "customCardOrder"
    public static let acknowledgedDetections = "acknowledgedDetectionNotices"
}

// MARK: - Card order

public enum CardOrderMode: String, Sendable { case recent, custom }

public enum CardOrdering {
    /// Last actual activity, newest first; unknown after known; ties by name, then id.
    public static func recent(_ providers: [Provider]) -> [String] {
        providers.sorted { a, b in
            switch (a.lastActivityAt, b.lastActivityAt) {
            case let (x?, y?) where x != y: return x > y
            case (.some, nil): return true
            case (nil, .some): return false
            default:
                if a.name != b.name { return a.name < b.name }
                return a.id < b.id
            }
        }.map(\.id)
    }

    /// `base` ids first (unknown ids kept, so hidden or removed instances keep their slot),
    /// then instances missing from `base` in recent order.
    public static func merged(_ base: [String], with providers: [Provider]) -> [String] {
        var seen = Set<String>()
        var out = base.filter { seen.insert($0).inserted }
        let missing = providers.filter { !seen.contains($0.id) }
        out += recent(missing)
        return out
    }

    /// Providers in `order`, ids without a provider dropped.
    public static func arrange(_ providers: [Provider], by order: [String]) -> [Provider] {
        let byId = Dictionary(providers.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        return merged(order, with: providers).compactMap { byId[$0] }
    }
}

// MARK: - Reset countdown

public struct ResetCountdown: Equatable, Sendable {
    /// "Resets in 3 hours", "Reset due · awaiting update", "Reset time unavailable".
    public var text: String
    /// Local weekday, full date, time and zone, e.g. "Sunday 15 November 2026 at 14:00 GMT"; nil when unknown.
    public var exact: String?
}

public extension Format {
    /// The one reset countdown used by overview, detail and History. Pure timestamp
    /// arithmetic with floored units; never calendar-day subtraction.
    static func resetCountdown(_ date: Date?, now: Date, timeZone: TimeZone = .current,
                               locale: Locale = .current) -> ResetCountdown {
        guard let date else { return ResetCountdown(text: "Reset time unavailable", exact: nil) }
        let exact = exactMoment(date, timeZone: timeZone, locale: locale)
        let seconds = date.timeIntervalSince(now)
        let text: String
        if seconds <= 0 {
            text = "Reset due · awaiting update"
        } else if seconds < 60 {
            text = "Resets in less than a minute"
        } else {
            text = "Resets in \(spelledDuration(seconds))"
        }
        return ResetCountdown(text: text, exact: exact)
    }

    /// Floored single unit: "7 days", "1 day", "23 hours", "1 minute". Under a minute: "less than a minute".
    static func spelledDuration(_ seconds: TimeInterval) -> String {
        let s = Int(seconds.rounded(.down))
        func unit(_ n: Int, _ word: String) -> String { "\(n) \(word)\(n == 1 ? "" : "s")" }
        if s >= 86_400 { return unit(s / 86_400, "day") }
        if s >= 3_600 { return unit(s / 3_600, "hour") }
        if s >= 60 { return unit(s / 60, "minute") }
        return "less than a minute"
    }

    static func exactMoment(_ date: Date, timeZone: TimeZone = .current, locale: Locale = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        formatter.dateStyle = .full
        formatter.timeStyle = .short
        let zone = DateFormatter()
        zone.locale = locale
        zone.timeZone = timeZone
        zone.dateFormat = "zzz"
        return "\(formatter.string(from: date)) \(zone.string(from: date))"
    }
}

// MARK: - Header facts

/// Is the collector watching? Independent of how the last check went.
public struct MonitoringFact: Equatable, Sendable {
    public enum State: Equatable, Sendable {
        case monitoring, checking, paused, stopped, notInstalled, waitingFirstRun, unavailable
    }
    public var state: State
    public var symbol: String
    public var text: String
    public var accessibilityText: String
    public var tooltip: String
}

/// How the last check went. Independent of whether monitoring continues.
public struct CollectionResultFact: Equatable, Sendable {
    public enum Kind: Equatable, Sendable { case noActiveProviders, success, attention, failed, none }
    public enum Severity: Equatable, Sendable { case neutral, ok, warning, critical }
    public var kind: Kind
    public var severity: Severity
    public var symbol: String
    /// "4 of 4 active providers updated", "4 providers checked", "No active providers".
    public var text: String
    /// "2 fresh · 1 stale · 1 failed"; empty when there is nothing to break down.
    public var detail: String
    public var accessibilityText: String
}

public enum HeaderFacts {
    static func ago(_ date: Date, now: Date) -> String {
        let seconds = now.timeIntervalSince(date)
        return seconds < 60 ? "just now" : "\(Format.spelledDuration(seconds)) ago"
    }

    public static func monitoring(_ report: Report?, collecting: Bool, now: Date) -> MonitoringFact {
        func fact(_ state: MonitoringFact.State, _ symbol: String, _ text: String, _ tooltip: String) -> MonitoringFact {
            MonitoringFact(state: state, symbol: symbol, text: text, accessibilityText: text, tooltip: tooltip)
        }
        if collecting {
            return fact(.checking, "arrow.triangle.2.circlepath", "Checking providers…",
                        "A check is running; values from the last check stay visible")
        }
        guard let report else {
            return fact(.unavailable, "questionmark.circle", "Collector status unavailable",
                        "The collector's report couldn't be read")
        }
        let checked = report.collection.lastAttemptAt.map { "checked \(ago($0, now: now))" }
        switch report.service.state {
        case .stopped:
            let atLogin = report.service.disabled == true ? " · stays stopped at login" : ""
            return fact(.stopped, "stop.circle", ["Stopped", checked].compactMap { $0 }.joined(separator: " · ") + atLogin,
                        "The collector isn't running; no scheduled checks happen")
        case .notInstalled:
            return fact(.notInstalled, "exclamationmark.circle", "Collector not installed",
                        "Install the collector to start monitoring")
        default: break
        }
        guard let checked else {
            return fact(.waitingFirstRun, "hourglass", "Waiting for the first check", "Nothing has been measured yet")
        }
        if report.schedule.isPaused {
            return fact(.paused, "pause.circle", "Paused · \(checked)", "Scheduled checks are paused; Collect now still works")
        }
        var text = "Monitoring · \(checked)"
        if let next = report.schedule.nextScheduledAt {
            let seconds = next.timeIntervalSince(now)
            text += seconds <= 0 ? " · next check due"
                : seconds < 60 ? " · next check in less than a minute"
                : " · next check in \(Format.spelledDuration(seconds))"
        }
        return fact(.monitoring, "checkmark.circle", text, "Checks run every \(report.schedule.intervalMinutes) minutes")
    }

    public static func result(_ report: Report?) -> CollectionResultFact {
        func fact(_ kind: CollectionResultFact.Kind, _ text: String, _ detail: String = "") -> CollectionResultFact {
            let (severity, symbol): (CollectionResultFact.Severity, String) = {
                switch kind {
                case .success: return (.ok, "checkmark.circle.fill")
                case .attention: return (.warning, "exclamationmark.triangle.fill")
                case .failed: return (.critical, "xmark.octagon.fill")
                case .noActiveProviders, .none: return (.neutral, "minus.circle")
                }
            }()
            return CollectionResultFact(kind: kind, severity: severity, symbol: symbol, text: text, detail: detail,
                                        accessibilityText: detail.isEmpty ? text : "\(text), \(detail)")
        }
        func providers(_ n: Int) -> String { "\(n) provider\(n == 1 ? "" : "s")" }
        func breakdown(_ parts: [(Int, String)]) -> String {
            parts.filter { $0.0 > 0 }.map { "\($0.0) \($0.1)" }.joined(separator: " · ")
        }
        guard let collection = report?.collection, collection.lastAttemptAt != nil || collection.summary != nil else {
            return fact(.none, "No checks yet")
        }
        let legacyKind: CollectionResultFact.Kind = {
            switch collection.lastAttemptResult {
            case .success: return .success
            case .partial: return .attention
            case .failed: return .failed
            case nil: return .none
            }
        }()
        guard let summary = collection.summary else {
            return fact(legacyKind, legacyKind == .failed ? "Last check failed" : "Last check finished")
        }
        guard let expected = summary.providersExpected else {
            // Older collectors: no freshness distinction, so never claim "updated".
            return fact(legacyKind, "\(providers(summary.providersAttempted)) checked",
                        breakdown([(summary.providersUpdated, "updated"), (summary.providersPartly, "partly"),
                                   (summary.providersFailed, "failed")]))
        }
        if expected == 0 { return fact(.noActiveProviders, "No active providers") }
        let fresh = summary.providersFresh ?? 0
        let stale = summary.providersStale ?? 0
        if fresh == expected { return fact(.success, "\(fresh) of \(expected) active providers updated") }
        let detail = breakdown([(fresh, "fresh"), (stale, "stale"), (summary.providersPartly, "partly"),
                                (summary.providersFailed, "failed")])
        let allFailed = fresh == 0 && stale == 0 && summary.providersPartly == 0 && summary.providersFailed > 0
        return fact(allFailed ? .failed : .attention, "\(providers(expected)) checked", detail)
    }
}
