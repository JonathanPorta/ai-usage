import Foundation

/// Today's value on a provider card. Missing, measured zero, a partial day and a
/// provider that was never collected stay distinct.
public struct TodayFigure: Equatable, Sendable {
    public enum State: Equatable, Sendable { case neverCollected, missing, zero, partial, measured }
    public var state: State
    /// "821K", "0" or "—" (never a zero for a missing reading).
    public var value: String
    /// "today so far", "no reading today", "measured · no usage today", "no usage recorded yet".
    public var note: String
}

/// The statistics popover behind a card's trophy button. Everything is derived from the
/// days in the report; nothing is inferred about history the report does not cover.
public struct ProviderStatistics: Equatable, Sendable {
    public var today: TodayFigure
    /// "Recorded total". "Lifetime" is reserved for complete coverage, which reports do not prove.
    public var totalLabel: String
    /// Sum of every recorded day in the report (measured, zero and partial days).
    public var recordedTotal: String
    /// "9 Sep – 29 Sep 2026", or "No days recorded".
    public var coverage: String
    /// "3 days missing"; nil when none are missing.
    public var missingDays: String? = nil
    public var rangeDays: Int
    /// "1.15M"; nil when no complete day in the range has usage.
    public var peakValue: String?
    /// "Thu 24 Sep".
    public var peakDay: String?
    /// "16:27", "yesterday 19:38", "27 Sep 19:38", or "Not reported".
    public var lastActivity: String
    /// "Total tokens" or "Input + output tokens".
    public var basis: String
    /// "Cache reads counted separately: 1.2M today"; nil when the provider reports no cache tokens today.
    public var cacheNote: String?

    public var accessibilitySummary: String {
        var parts = ["Today \(today.value), \(today.note)",
                     "\(totalLabel) \(recordedTotal), \(coverage)" + (missingDays.map { ", \($0)" } ?? "")]
        if let peakValue, let peakDay { parts.append("Peak \(peakValue) on \(peakDay), last \(rangeDays) days") }
        parts.append("Last activity \(lastActivity)")
        parts.append(basis)
        if let cacheNote { parts.append(cacheNote) }
        return parts.joined(separator: ". ")
    }
}

public enum CardStatistics {
    public static func today(_ provider: Provider) -> TodayFigure {
        let everRecorded = provider.days.contains(where: \.hasValue) || provider.today != nil
        guard let today = provider.today else {
            if !everRecorded {
                return TodayFigure(state: .neverCollected, value: "—", note: "no usage recorded yet")
            }
            return TodayFigure(state: .missing, value: "—", note: "no reading today")
        }
        guard let total = today.total else {
            return TodayFigure(state: .missing, value: "—", note: "no reading today")
        }
        if today.partial { return TodayFigure(state: .partial, value: Format.tokens(total), note: "today so far") }
        if total == 0 { return TodayFigure(state: .zero, value: "0", note: "measured · no usage today") }
        return TodayFigure(state: .measured, value: Format.tokens(total), note: "today")
    }

    /// Short label under a card's mini chart: "7 days · total tokens".
    public static func rangeLabel(_ provider: Provider, days: Int) -> String {
        "\(days) days · \(Presentation.seriesLabel(provider))"
    }

    public static func statistics(_ provider: Provider, rangeDays: Int = 7, now: Date,
                                  calendar: Calendar = .current) -> ProviderStatistics {
        let recorded = provider.days.filter(\.hasValue)
        let total = recorded.reduce(0) { $0 + ($1.total ?? 0) }
        var coverage = "No days recorded"
        var missingDays: String?
        if let first = recorded.first, let last = recorded.last,
           let start = provider.days.firstIndex(where: { $0.date == first.date }),
           let end = provider.days.firstIndex(where: { $0.date == last.date }) {
            let missing = provider.days[start...end].filter { $0.state == .missing }.count
            coverage = first.date == last.date ? dayWithYear(first.date) : "\(Format.dayLabel(first.date)) – \(dayWithYear(last.date))"
            if missing > 0 { missingDays = "\(missing) day\(missing == 1 ? "" : "s") missing" }
        }
        let range = Presentation.recentDays(provider, count: rangeDays)
        let peak = range.filter { $0.state == .measured }.max { ($0.total ?? 0) < ($1.total ?? 0) }
        let hasPeak = (peak?.total ?? 0) > 0
        var cacheNote: String?
        if let cache = provider.today?.cacheRead, cache > 0 {
            cacheNote = "Cache reads counted separately: \(Format.tokens(cache)) today"
        }
        return ProviderStatistics(
            today: today(provider),
            totalLabel: "Recorded total",
            recordedTotal: recorded.isEmpty ? "—" : Format.tokens(total),
            coverage: coverage,
            missingDays: missingDays,
            rangeDays: rangeDays,
            peakValue: hasPeak ? Format.tokens(peak?.total) : nil,
            peakDay: hasPeak ? peak.map { Format.longDay($0.date) } : nil,
            lastActivity: provider.lastActivityAt.map { Format.moment($0, now: now, calendar: calendar) } ?? "Not reported",
            basis: provider.usage.hasSplit ? "Input + output tokens" : "Total tokens",
            cacheNote: cacheNote)
    }

    private static func dayWithYear(_ date: String) -> String {
        guard let parsed = ReportDate.day(date) else { return date }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB")
        formatter.dateFormat = "d MMM yyyy"
        return formatter.string(from: parsed)
    }
}
