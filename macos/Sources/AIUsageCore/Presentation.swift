import Foundation

/// Text formatting shared by every view. Pure functions of their inputs (and `now`).
public enum Format {
    public static func tokens(_ value: Double?) -> String {
        guard let value else { return "—" }
        let magnitude = abs(value)
        func trimmed(_ number: Double, _ suffix: String) -> String {
            let digits = number >= 100 ? 0 : number >= 10 ? 1 : 2
            var text = String(format: "%.\(digits)f", number)
            if text.contains(".") {
                while text.hasSuffix("0") { text.removeLast() }
                if text.hasSuffix(".") { text.removeLast() }
            }
            return text + suffix
        }
        switch magnitude {
        case 1_000_000_000...: return trimmed(value / 1_000_000_000, "B")
        case 1_000_000...: return trimmed(value / 1_000_000, "M")
        case 10_000...: return "\(Int((value / 1000).rounded()))K"
        case 1000...: return trimmed(value / 1000, "K")
        default: return "\(Int(value.rounded()))"
        }
    }

    public static func percent(_ value: Double?) -> String {
        guard let value else { return "—" }
        if value > 0 && value < 1 { return "<1%" }
        return "\(Int(value.rounded()))%"
    }

    public static func usd(_ value: Double) -> String { String(format: "$%.2f", value) }

    public static func duration(_ seconds: TimeInterval) -> String {
        let minutes = Int((seconds / 60).rounded(.down))
        if minutes < 1 { return "less than a minute" }
        if minutes < 60 { return "\(minutes) min" }
        let hours = minutes / 60
        let rest = minutes % 60
        if hours < 3 && rest > 0 { return "\(hours) h \(rest) min" }
        if hours < 48 { return "\(hours) h" }
        return "\(hours / 24) days"
    }

    public static func ago(_ date: Date?, now: Date) -> String {
        guard let date else { return "never" }
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return "just now" }
        return "\(duration(seconds)) ago"
    }

    public static func until(_ date: Date, now: Date) -> String {
        let seconds = date.timeIntervalSince(now)
        if seconds <= 0 { return "due now" }
        if seconds < 60 { return "in under a minute" }
        return "in \(duration(seconds))"
    }

    private static func formatter(_ pattern: String) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB")
        formatter.dateFormat = pattern
        return formatter
    }

    public static func clock(_ date: Date) -> String { formatter("HH:mm").string(from: date) }

    /// "10:38" today, "yesterday 19:38", otherwise "27 Sep 19:38".
    public static func moment(_ date: Date?, now: Date, calendar: Calendar = .current) -> String {
        guard let date else { return "—" }
        if calendar.isDate(date, inSameDayAs: now) { return clock(date) }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(date, inSameDayAs: yesterday) {
            return "yesterday \(clock(date))"
        }
        return formatter("d MMM HH:mm").string(from: date)
    }

    /// Reset times: "in 1 h 42 min" within 6 h, otherwise "Fri 09:00".
    public static func reset(_ date: Date, now: Date) -> String {
        let seconds = date.timeIntervalSince(now)
        if seconds > 0 && seconds < 6 * 3600 { return "in \(duration(seconds))" }
        return formatter("EEE HH:mm").string(from: date)
    }

    public static func dayLabel(_ date: String) -> String {
        guard let parsed = ReportDate.day(date) else { return date }
        return formatter("d MMM").string(from: parsed)
    }

    public static func weekday(_ date: String) -> String {
        guard let parsed = ReportDate.day(date) else { return date }
        return formatter("EEE").string(from: parsed)
    }

    public static func longDay(_ date: String) -> String {
        guard let parsed = ReportDate.day(date) else { return date }
        return formatter("EEE d MMM").string(from: parsed)
    }
}

// MARK: - Status row

public struct StatusSummary: Equatable {
    public enum Tone: Equatable { case healthy, collecting, attention, stopped, waiting, paused }
    public var tone: Tone
    public var title: String
    public var detail: String
}

public enum Presentation {
    public static func status(_ report: Report?, collecting: Bool, now: Date, progress: CollectProgress? = nil) -> StatusSummary {
        if collecting {
            if let progress, let name = report?.provider(progress.providerId)?.name {
                return StatusSummary(tone: .collecting, title: "Checking providers…",
                                     detail: "Reading \(name) · \(progress.index) of \(progress.total)")
            }
            return StatusSummary(tone: .collecting, title: "Checking providers…",
                                 detail: "Values from the last check stay visible")
        }
        guard let report else {
            return StatusSummary(tone: .waiting, title: "Reading collector data…", detail: "")
        }
        let collection = report.collection
        let checked = collection.lastAttemptAt.map { "Last check \(Format.ago($0, now: now))" } ?? "No checks yet"
        switch report.service.state {
        case .stopped:
            let atLogin = report.service.disabled == true ? " · stays stopped at login" : ""
            return StatusSummary(tone: .stopped, title: "Collector stopped", detail: checked + atLogin)
        case .notInstalled:
            return StatusSummary(tone: .stopped, title: "Collector not installed", detail: checked)
        default: break
        }
        guard let attempt = collection.lastAttemptAt else {
            return StatusSummary(tone: .waiting, title: "Waiting for the first check", detail: "Nothing has been measured yet")
        }
        if report.schedule.isPaused {
            return StatusSummary(tone: .paused, title: "Scheduled checks paused",
                                 detail: "\(checked) · Collect now still works")
        }
        if collection.lastAttemptResult == .failed {
            return StatusSummary(tone: .attention, title: "Last check failed", detail: "\(Format.ago(attempt, now: now))")
        }
        var detail = "Checked \(Format.ago(attempt, now: now))"
        if let next = report.schedule.nextScheduledAt {
            detail += next > now ? " · next \(Format.until(next, now: now))" : " · next check due"
        }
        return StatusSummary(tone: .healthy, title: "Monitoring", detail: detail)
    }

    // MARK: Notices

    public struct Notice: Equatable, Identifiable {
        public enum Tone: Equatable { case failure, limit, stale, info }
        public var id: String
        public var tone: Tone
        public var title: String
        public var detail: String
        public var providerId: String?

        public init(id: String, tone: Tone, title: String, detail: String, providerId: String?) {
            self.id = id
            self.tone = tone
            self.title = title
            self.detail = detail
            self.providerId = providerId
        }
    }

    /// Actionable conditions, most important first. The popover shows the first
    /// and collapses the rest into "+N more".
    public static func notices(_ report: Report, now: Date) -> [Notice] {
        var out: [Notice] = []
        for failure in report.collection.failures {
            let name = failure.provider.flatMap { report.provider($0)?.name }
            if failure.kind == "collector" {
                out.append(Notice(id: "collector", tone: .failure, title: "The last check failed",
                                  detail: (failure.message ?? "") + " · Nothing was changed. The collector log has details.",
                                  providerId: nil))
            } else if let name {
                let what = failure.kind == "quota" ? "quota" : failure.kind == "usage" ? "usage" : "check"
                out.append(Notice(
                    id: "fail-\(failure.provider ?? "")-\(failure.source ?? "")",
                    tone: .failure,
                    title: failure.auth ? "\(name) needs you to sign in" : "\(name) \(what) check failed",
                    detail: "\(failure.message ?? "No details") · cached values are kept",
                    providerId: failure.provider))
            }
        }
        for provider in report.providers where provider.enabled {
            for window in provider.activeWindows where window.limit == .reached {
                let reset = window.resetsAt.map { " · resets \(Format.reset($0, now: now))" } ?? ""
                out.append(Notice(id: "limit-\(provider.id)-\(window.id)", tone: .limit,
                                  title: "\(provider.name) \(window.short.lowercased()) limit reached",
                                  detail: "Measured \(Format.ago(window.measuredAt, now: now))\(reset)",
                                  providerId: provider.id))
            }
        }
        for provider in report.providers where provider.enabled {
            for window in provider.activeWindows where window.limit == .low {
                out.append(Notice(id: "low-\(provider.id)-\(window.id)", tone: .limit,
                                  title: "\(provider.name) \(window.short.lowercased()) limit almost used",
                                  detail: "\(quotaValue(window)) \(quotaBasis(window))", providerId: provider.id))
            }
        }
        return out
    }

    // MARK: Quota cells

    public static func quotaValue(_ window: QuotaWindow) -> String { Format.percent(window.percent) }

    public static func quotaBasis(_ window: QuotaWindow) -> String { window.basis == .remaining ? "remaining" : "used" }

    /// Fraction of the meter to fill: remaining for remaining-basis, used for used-basis.
    public static func meterFraction(_ window: QuotaWindow) -> Double {
        max(0, min(1, (window.percent ?? 0) / 100))
    }

    public static func quotaCaption(_ window: QuotaWindow, now: Date) -> (text: String, warning: Bool) {
        let since = Format.moment(window.measuredAt, now: now)
        switch window.staleCause {
        case .auth?: return ("Sign-in needed · last reading \(since)", true)
        case .failed?: return ("Check failed · last reading \(since)", true)
        case .notCollected?: return ("Not checked since \(since)", true)
        case .sourceOld?:
            return (window.omitted ? "Not reported since \(since)" : "No reading since \(since)", true)
        case .resetPassed?:
            let reset = window.resetsAt.map { Format.moment($0, now: now) } ?? "—"
            return ("Reset at \(reset) · no reading since", true)
        case nil:
            if let resets = window.resetsAt { return ("Resets \(Format.reset(resets, now: now))", false) }
            return ("Measured \(Format.ago(window.measuredAt, now: now))", false)
        }
    }

    public static func usageCaption(_ provider: Provider, now: Date) -> (text: String, warning: Bool) {
        let usage = provider.usage
        switch usage.staleCause {
        // The failed source itself is shown in red by the card; this line only dates the kept values.
        case .failed?: return ("Usage from the last good read, \(Format.moment(usage.collectedAt, now: now))", false)
        case .notCollected?: return ("Usage not checked since \(Format.moment(usage.collectedAt, now: now))", true)
        case .sourceOld?: return ("Usage reading from \(Format.moment(usage.measuredAt, now: now))", true)
        default: break
        }
        if usage.status == .none { return ("No usage recorded yet", false) }
        if usage.mode == "event" {
            return ("Checked \(Format.ago(usage.collectedAt, now: now)) · last activity \(Format.moment(usage.measuredAt, now: now))", false)
        }
        return ("Measured \(Format.ago(usage.measuredAt, now: now))", false)
    }

    public static func quotaUnavailableText(_ provider: Provider) -> String? {
        switch provider.quota.status {
        case .unsupported, .unavailable: return "Quota not available"
        case .none: return provider.setup == .ready ? "No quota reading yet" : nil
        default: return nil
        }
    }

    public static func setupText(_ provider: Provider) -> String? {
        switch provider.setup {
        case .ready: return nil
        case .notDetected: return provider.setupNote ?? "Not found on this Mac."
        case .disabled: return provider.setupNote ?? "Turned off in the collector config."
        case .notConfigured: return provider.setupNote ?? "Not set up."
        case .waiting: return provider.setupNote ?? "Waiting for the first reading."
        }
    }

    // MARK: Days

    public static func recentDays(_ provider: Provider, count: Int) -> [Day] {
        Array(provider.days.suffix(count))
    }

    public static func seriesLabel(_ provider: Provider) -> String {
        provider.usage.hasSplit ? "input + output" : "total tokens"
    }

    /// "7 days · input + output · peak 1.15M Thu"
    public static func chartCaption(_ provider: Provider, days: [Day]) -> String {
        let measured = days.filter { $0.hasValue && $0.state != .partial }
        var text = "\(days.count) days · \(seriesLabel(provider))"
        if let peak = measured.max(by: { ($0.total ?? 0) < ($1.total ?? 0) }), (peak.total ?? 0) > 0 {
            text += " · peak \(Format.tokens(peak.total)) \(Format.weekday(peak.date))"
        }
        return text
    }

    public static func readout(_ provider: Provider, day: Day, asOf: Date?) -> String {
        let date = Format.longDay(day.date)
        switch day.state {
        case .missing: return "\(date): no data — missing reading, not zero"
        case .notCollected: return "\(date): not collected (before \(provider.name) data began)"
        case .zero: return "\(date): 0 tokens — measured, no usage"
        case .partial:
            let time = asOf.map { " (as of \(Format.clock($0)))" } ?? ""
            return "Today so far\(time): \(tokenBreakdown(provider, day))"
        case .measured:
            var text = "\(date): \(tokenBreakdown(provider, day))"
            if day.incompleteEvents > 0 { text += " · \(day.incompleteEvents) turns reported incomplete usage" }
            return text
        }
    }

    public static func tokenBreakdown(_ provider: Provider, _ day: Day) -> String {
        if provider.usage.hasSplit, let input = day.input, let output = day.output {
            return "\(Format.tokens(input + output)) tokens (\(Format.tokens(input)) in · \(Format.tokens(output)) out)"
        }
        return "\(Format.tokens(day.total)) tokens"
    }

    public static func sum(_ days: [Day]) -> Double {
        days.reduce(0) { $0 + ($1.hasValue ? ($1.total ?? 0) : 0) }
    }
}

// MARK: - Data age and refresh state

public enum DataStatus {
    /// "Data read 3 min ago", "Refreshing…", or the refresh error, for the footer and Monitoring.
    public static func text(generatedAt: Date?, refreshing: Bool, error: String?, cached: Bool, now: Date) -> (text: String, warning: Bool) {
        if refreshing { return ("Reading collector data…", false) }
        if let error { return ("Couldn’t refresh: \(error)", true) }
        guard let generatedAt else { return ("No data yet", false) }
        let age = Format.ago(generatedAt, now: now)
        return (cached ? "Showing saved data from \(age)" : "Data read \(age)", cached)
    }
}

// MARK: - Monitoring copy

public enum MonitoringText {
    public static func service(_ service: Service) -> (title: String, detail: String) {
        switch service.state {
        case .running:
            return ("Running", service.pid.map { "Background collector (pid \($0)) · keeps running when this app quits" }
                    ?? "Keeps running when this app quits")
        case .loaded: return ("Loaded, not running", "launchd has the collector but it isn’t running right now")
        case .stopped:
            return ("Stopped", service.disabled == true
                    ? "Stays stopped, including after you log in, until you start it. Data and settings are kept."
                    : "Not running. Data and settings are kept.")
        case .notInstalled: return ("Not installed", "Install it from the repository: python3 ai_usage_service.py install")
        case .unknown: return ("Unknown", service.detail)
        }
    }

    public static func schedule(_ report: Report, now: Date) -> (title: String, detail: String) {
        let every = "Every \(Format.duration(Double(report.schedule.intervalMinutes * 60)))"
        if report.service.state != .running {
            return ("No scheduled checks", "The collector isn’t running. Collect now still runs a one-time check.")
        }
        if report.schedule.isPaused {
            return ("Paused", "Scheduled checks are skipped; the collector keeps running and Collect now still works.")
        }
        if let next = report.schedule.nextScheduledAt {
            return (every, next > now ? "Next check \(Format.until(next, now: now)) (\(Format.clock(next)))" : "Next check is due")
        }
        return (every, "Next check time not known yet")
    }

    /// Why Pause can't be offered, if it can't.
    public static func pauseUnavailable(_ report: Report) -> String? {
        if report.collector.capabilities?.pause == true { return nil }
        let installed = report.collector.installedVersion ?? "unknown"
        var text = "Pause needs collector 2.2.0 or newer (installed: \(installed)). Reinstall it from the repository with python3 ai_usage_service.py install to enable pausing."
        if report.schedule.pauseRequested == true {
            text += " config.json already asks for a pause, but the installed collector ignores it."
        }
        return text
    }

    public static func lastAttempt(_ collection: CollectionState, now: Date) -> String {
        guard let at = collection.lastAttemptAt else { return "No checks yet" }
        let result: String
        switch collection.lastAttemptResult {
        case .success?: result = "complete"
        case .partial?: result = "partly complete"
        case .failed?: result = "failed"
        case nil: result = "unknown result"
        }
        return "Last check \(Format.moment(at, now: now)) · \(result)"
    }
}

// MARK: - Notifications

public struct NotificationPreferences: Equatable, Sendable {
    public var limits: Bool
    public var failures: Bool
    public init(limits: Bool, failures: Bool) {
        self.limits = limits
        self.failures = failures
    }
}

public struct PlannedAlert: Equatable, Sendable {
    public var key: String
    public var title: String
    public var body: String
}

/// Decides which notifications a snapshot warrants. Keys make each one fire
/// once: per window and reset period for limits, per check for failures.
public enum NotificationPlanner {
    public static func alerts(for report: Report, now: Date, preferences: NotificationPreferences,
                              delivered: Set<String>) -> [PlannedAlert] {
        var out: [PlannedAlert] = []
        if preferences.limits {
            for provider in report.providers where provider.enabled {
                for window in provider.activeWindows where window.status == .current && window.limit == .reached {
                    let period = window.resetsAt.map(ReportDate.format) ?? "none"
                    let key = "limit|\(provider.id)|\(window.id)|\(period)"
                    let reset = window.resetsAt.map { " It resets \(Format.reset($0, now: now))." } ?? ""
                    out.append(PlannedAlert(key: key, title: "\(provider.name) \(window.short.lowercased()) limit reached",
                                            body: "Measured \(Format.moment(window.measuredAt, now: now)).\(reset)"))
                }
            }
        }
        if preferences.failures, let attempt = report.collection.lastAttemptAt, now.timeIntervalSince(attempt) < 2 * 3600 {
            for failure in report.collection.failures {
                let key = "failure|\(failure.provider ?? "collector")|\(failure.source ?? "")|\(ReportDate.format(attempt))"
                let name = failure.provider.flatMap { report.provider($0)?.name } ?? "The collector"
                let title = failure.auth ? "\(name) needs you to sign in" :
                    (failure.kind == "collector" ? "The last check failed" : "\(name) check failed")
                out.append(PlannedAlert(key: key, title: title, body: (failure.message ?? "") + " Cached values are kept."))
            }
        }
        return out.filter { !delivered.contains($0.key) }
    }
}
