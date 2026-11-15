import Foundation

// MARK: - Monitoring page model

/// What the Monitoring page shows, derived from the report. Kept here so the
/// grouping, status mapping and last-success rule are testable without SwiftUI.
public enum MonitoringPage {
    /// A source's compact status: a symbol plus a short label, never color alone.
    public struct SourceBadge: Equatable, Sendable {
        public enum Kind: Equatable, Sendable { case healthy, stale, failed, missing, signIn, off, waiting }
        public var kind: Kind
        public var symbol: String
        /// Empty for healthy: the checkmark alone carries it, with `accessibilityLabel` for VoiceOver.
        public var label: String
        public var accessibilityLabel: String
        public var isProblem: Bool { kind == .stale || kind == .failed || kind == .signIn }
    }

    public static func badge(_ status: Source.Status) -> SourceBadge {
        switch status {
        case .ok: return SourceBadge(kind: .healthy, symbol: "checkmark.circle", label: "", accessibilityLabel: "Healthy")
        case .stale: return SourceBadge(kind: .stale, symbol: "clock.badge.exclamationmark", label: "Stale", accessibilityLabel: "Stale")
        case .failed: return SourceBadge(kind: .failed, symbol: "xmark.circle", label: "Failed", accessibilityLabel: "Failed")
        case .notDetected: return SourceBadge(kind: .missing, symbol: "questionmark.circle", label: "Missing", accessibilityLabel: "Missing")
        case .auth: return SourceBadge(kind: .signIn, symbol: "lock", label: "Sign in", accessibilityLabel: "Sign-in required")
        case .off: return SourceBadge(kind: .off, symbol: "minus.circle", label: "Off", accessibilityLabel: "Off")
        case .waiting: return SourceBadge(kind: .waiting, symbol: "hourglass", label: "Waiting", accessibilityLabel: "Waiting")
        }
    }

    public struct SourceRow: Equatable, Identifiable, Sendable {
        public var id: String
        public var label: String
        public var badge: SourceBadge
        /// Exact read/measurement times and any error, for the tooltip and expansion.
        public var detail: String
    }

    public struct SourceGroup: Equatable, Identifiable, Sendable {
        public var id: String
        public var name: String
        public var rows: [SourceRow]
        public var problemCount: Int { rows.filter { $0.badge.isProblem }.count }
    }

    /// Exact times for one source; read and measurement stay distinct.
    public static func sourceDetail(_ source: Source) -> String {
        var parts: [String] = []
        if let read = source.lastReadAt { parts.append("Read \(Format.exactMoment(read))") } else { parts.append("Never read") }
        if let measured = source.measuredAt, measured != source.lastReadAt {
            parts.append("measured \(Format.exactMoment(measured))")
        }
        if let attempt = source.lastAttemptAt, attempt != source.lastReadAt {
            parts.append("last tried \(Format.exactMoment(attempt))")
        }
        if let error = source.error, !error.isEmpty { parts.append(error) }
        return parts.joined(separator: " · ")
    }

    /// Whether a provider takes part in collection: effectively active and not turned off.
    /// Awaiting detection (pending) is not yet collected, so it is inactive. The collection
    /// result's denominator uses this same rule.
    public static func isActive(_ provider: Provider) -> Bool {
        provider.trackingMode != .off && provider.effectiveTracking == .active
    }

    /// One provider's health from its sources: the worst source wins. Off sources don't count.
    public enum ProviderHealth: Int, Comparable, Sendable {
        case fresh, waiting, stale, missing, signIn, failed
        public static func < (a: Self, b: Self) -> Bool { a.rawValue < b.rawValue }
    }

    public static func health(_ provider: Provider) -> ProviderHealth {
        provider.sources.map { source -> ProviderHealth in
            switch source.status {
            case .ok, .off: return .fresh
            case .waiting: return .waiting
            case .stale: return .stale
            case .notDetected: return .missing
            case .auth: return .signIn
            case .failed: return .failed
            }
        }.max() ?? .fresh
    }

    /// Provider/account groups of sources, split into active and inactive. Report order is kept.
    public static func sourceGroups(_ report: Report) -> (active: [SourceGroup], inactive: [SourceGroup]) {
        var active: [SourceGroup] = []
        var inactive: [SourceGroup] = []
        for provider in report.providers {
            let rows = provider.sources.map {
                SourceRow(id: $0.id, label: $0.label, badge: badge($0.status), detail: sourceDetail($0))
            }
            let group = SourceGroup(id: provider.id, name: provider.name, rows: rows)
            if isActive(provider) { active.append(group) } else { inactive.append(group) }
        }
        return (active, inactive)
    }

    /// The last successful time, only when it differs meaningfully from the last attempt:
    /// more than five minutes older than the last attempt (or there is no attempt).
    public static func lastSuccessWorthShowing(_ collection: CollectionState) -> Date? {
        guard let success = collection.lastSuccessAt else { return nil }
        guard let attempt = collection.lastAttemptAt else { return success }
        return attempt.timeIntervalSince(success) > 5 * 60 ? success : nil
    }

    /// Exclusions for the expanded collection view: skipped providers keep cached values.
    public static func exclusions(_ report: Report) -> String? {
        let skipped = report.collection.summary?.skipped ?? []
        let inactive = report.providers.filter { !isActive($0) }.map(\.name)
        let names = Array(NSOrderedSet(array: skipped + inactive)) as? [String] ?? skipped + inactive
        guard !names.isEmpty else { return nil }
        return "Not checked: \(names.joined(separator: ", ")) · their saved values are kept"
    }

    /// Service status line and the technical detail that lives behind a disclosure.
    public static func service(_ service: Service) -> (title: String, running: Bool, technical: String) {
        let technical = [service.pid.map { "PID \($0)" }, service.detail.isEmpty ? nil : service.detail,
                         service.disabled == true ? "launchd: disabled at login" : nil]
            .compactMap { $0 }.joined(separator: " · ")
        switch service.state {
        case .running: return ("Running", true, technical)
        case .loaded: return ("Loaded, not running", false, technical)
        case .stopped:
            return (service.disabled == true ? "Stopped · stays stopped at login" : "Stopped", false, technical)
        case .notInstalled: return ("Not installed", false, "Install with python3 ai_usage_service.py install")
        case .unknown: return ("Status unknown", false, technical)
        }
    }
}
