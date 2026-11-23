import Foundation

/// The chart's metric on provider detail and History.
public enum DetailMetric: Hashable, Sendable { case tokens, quota }

/// What History should show when opened from a provider's detail page.
public struct HistoryContext: Equatable, Sendable {
    public var providerId: String
    public var metric: DetailMetric
    /// One of History's token ranges (7, 30, 90).
    public var tokenRange: Int
    public var windowId: String?
    public var quotaRange: String?

    public init(providerId: String, metric: DetailMetric, tokenRange: Int, windowId: String? = nil, quotaRange: String? = nil) {
        self.providerId = providerId
        self.metric = metric
        self.tokenRange = tokenRange
        self.windowId = windowId
        self.quotaRange = quotaRange
    }
}

/// Quiet empty-state copy shared by provider detail and History.
public struct EmptyCopy: Equatable, Sendable {
    public enum Action: Equatable, Sendable {
        /// Switch this view's range to the given number of days.
        case widenRange(Int)
        /// Open History at this provider with the given range (detail only offers 7 and 30 days).
        case openHistoryRange(Int)
        case enableTracking
        case providerSettings
        case checkAgain

        public var title: String {
            switch self {
            case let .widenRange(days): return "Show \(days) days"
            case let .openHistoryRange(days): return "Show \(days) days in History"
            case .enableTracking: return "Turn on tracking"
            case .providerSettings: return "Provider settings…"
            case .checkAgain: return "Check again"
            }
        }
    }

    public var symbol: String
    public var title: String
    public var detail: String
    /// The primary action, and an optional second one shown beside it.
    public var action: Action?
    public var secondary: Action?

    public init(symbol: String, title: String, detail: String, action: Action?, secondary: Action? = nil) {
        self.symbol = symbol
        self.title = title
        self.detail = detail
        self.action = action
        self.secondary = secondary
    }
}

public enum DetailPresentation {
    public static let detailTokenRanges = [7, 30]
    public static let historyTokenRanges = [7, 30, 90]

    // MARK: History context

    /// Keeps the chosen metric; keeps the range when History offers it (else 30 days);
    /// keeps the quota window and range when the window still offers them.
    public static func historyContext(_ provider: Provider, metric: DetailMetric, tokenRange: Int,
                                      windowId: String?, quotaRange: String?) -> HistoryContext {
        let hasQuota = !provider.activeWindows.isEmpty && Presentation.quotaSupport(provider).supported
        let window = provider.activeWindows.first { $0.id == windowId } ?? provider.activeWindows.first
        let range = historyTokenRanges.contains(tokenRange) ? tokenRange : 30
        let useQuota = metric == .quota && hasQuota
        let quota = useQuota ? quotaRange.flatMap { window?.ranges.contains($0) == true ? $0 : nil } : nil
        return HistoryContext(providerId: provider.id, metric: useQuota ? .quota : .tokens, tokenRange: range,
                              windowId: useQuota ? window?.id : nil, quotaRange: quota)
    }

    // MARK: Tracking toggle

    public struct TrackingToggle: Equatable, Sendable {
        /// The switch position: the pending choice while saving, else what is in effect.
        public var isOn: Bool
        /// "On", "Off", "Auto · tracking", "Saving…".
        public var status: String
        /// Auto (not an explicit choice) is deciding the state.
        public var autoControlled: Bool
        /// Auto has not decided yet (first-run detection pending): neither on nor off.
        public var waiting: Bool = false
    }

    public static func trackingToggle(_ provider: Provider, pending: Bool?) -> TrackingToggle {
        let mode = provider.trackingMode
        let auto = mode == .auto || mode == .unknown
        let effectiveOn = auto ? provider.effectiveTracking == .active : mode == .on
        if let pending { return TrackingToggle(isOn: pending, status: "Saving…", autoControlled: false) }
        guard auto else { return TrackingToggle(isOn: effectiveOn, status: effectiveOn ? "On" : "Off", autoControlled: false) }
        if provider.effectiveTracking == .pending || provider.effectiveTracking == .unknown {
            return TrackingToggle(isOn: false, status: "Auto · waiting for detection", autoControlled: true, waiting: true)
        }
        let status = SettingsTracking.statusText(provider, shown: .auto, saving: false) ?? "Auto"
        return TrackingToggle(isOn: effectiveOn, status: status, autoControlled: true)
    }

    /// Off -> Off, on -> On. With an older installed collector (no tracking policy), which
    /// reads only `enabled` and collects Auto as On, turning on a provider saved as Auto keeps
    /// Auto (the writer removes `enabled`, so it is collected) instead of replacing it with On.
    public static func trackingChange(_ provider: Provider, on: Bool, supportsPolicy: Bool) -> ConfigChange {
        if !supportsPolicy, on, provider.trackingMode == .auto { return .tracking(provider, .auto) }
        return .tracking(provider, on ? .on : .off)
    }

    public static func supportsPolicy(_ report: Report?) -> Bool {
        report?.collector.capabilities?.trackingPolicy == true
    }

    public static func isTrackingOff(_ provider: Provider) -> Bool {
        provider.trackingMode == .off || provider.setup == .disabled
    }

    /// Not being collected now (Off, or Auto decided inactive): its saved values are last known, not current.
    public static func isNotCollecting(_ provider: Provider) -> Bool {
        isTrackingOff(provider) || (provider.trackingMode != .on && provider.effectiveTracking == .inactive)
    }

    /// Quota windows as shown on detail: when not collecting, each reads as last known
    /// ("Not checked since …", "Last known: resets in …"), never as current.
    /// The quota note under the windows, unless it would contradict them: a note that
    /// explains a kept reading is "not stale" is dropped while the quota shows Stale.
    public static func quotaNote(_ provider: Provider) -> String? {
        guard let note = provider.quota.note else { return nil }
        let showsStale = provider.quota.status == .stale || displayWindows(provider).contains { $0.status == .stale }
        if showsStale, note.lowercased().contains("not stale") { return nil }
        return note
    }

    public static func displayWindows(_ provider: Provider) -> [QuotaWindow] {
        guard isNotCollecting(provider) else { return provider.activeWindows }
        return provider.activeWindows.map { window in
            var window = window
            window.status = .stale
            if window.staleCause == nil || window.staleCause == .resetPassed { window.staleCause = .notCollected }
            return window
        }
    }

    // MARK: Empty states

    /// Token chart empty state, or nil when there is something to plot. `ranges`: the ranges
    /// this view offers; a wider one is offered first, then History's 90 days from detail.
    public static func chartEmpty(_ provider: Provider, days: [Day], range: Int, ranges: [Int]) -> EmptyCopy? {
        var availability = Presentation.historyAvailability(provider, days: days)
        if availability != .data, availability != .noneInRange, isTrackingOff(provider) { availability = .trackingDisabled }
        switch availability {
        case .data: return nil
        case .noneInRange:
            let action: EmptyCopy.Action? = ranges.first { $0 > range }.map { .widenRange($0) }
                ?? (range < 90 ? .openHistoryRange(90) : nil)
            return EmptyCopy(symbol: "calendar", title: "No data in this range",
                             detail: "\(provider.name) has earlier history, but nothing in the last \(range) days.",
                             action: action)
        case .trackingDisabled:
            return EmptyCopy(symbol: "pause.circle", title: "Tracking is off",
                             detail: "\(provider.name) isn't collected. Turning it on doesn't install anything.",
                             action: .enableTracking)
        case .notDetected:
            return EmptyCopy(symbol: "questionmark.circle", title: "\(provider.name) not detected",
                             detail: "Tracking is on, but \(provider.name) wasn't found on this Mac at the last check.",
                             action: .checkAgain, secondary: .providerSettings)
        case .notConfigured:
            return EmptyCopy(symbol: "wrench.and.screwdriver", title: "\(provider.name) is not set up",
                             detail: Presentation.setupText(provider) ?? "\(provider.name) needs setting up before it can be read.",
                             action: .providerSettings, secondary: .checkAgain)
        case .noUsageRecorded:
            let detail = provider.setup == .waiting
                ? (Presentation.setupText(provider) ?? "Waiting for the first reading.")
                : "Tracking is on and \(provider.name) was found; usage appears here once it is used."
            return EmptyCopy(symbol: "chart.bar", title: "No usage recorded yet", detail: detail, action: .checkAgain)
        }
    }

    public enum TodayState: Equatable, Sendable { case reading, noReadingToday, noUsageEver, notCollecting }

    public static func todayState(_ provider: Provider) -> TodayState {
        if isNotCollecting(provider) { return .notCollecting }
        if provider.today != nil { return .reading }
        return provider.days.contains(where: \.hasValue) ? .noReadingToday : .noUsageEver
    }

    public static let noReadingToday = EmptyCopy(symbol: "sun.horizon", title: "No reading for today yet",
                                                 detail: "Earlier days stay in the chart.", action: nil)

    public static let notCollectingToday = EmptyCopy(symbol: "pause.circle", title: "Not collecting today",
                                                     detail: "Tracking is off; saved history stays in the chart.", action: nil)

    /// Reported usage cost: its own empty state, never filled from a subscription price.
    /// The report's `costs.reports_cost` says whether the provider can report cost at all.
    /// Older collectors omit it; then a provider that never reports cost is recognised by
    /// its static note ("… does not report a usage cost"), and anything else gets the
    /// wording that claims least.
    public static func reportsNoCost(_ provider: Provider) -> Bool {
        if let reportsCost = provider.costs.reportsCost { return !reportsCost }
        guard let note = provider.costs.reportedNote?.lowercased() else { return false }
        return ["not report", "doesn't include", "does not include", "isn't reported", "not reported"].contains { note.contains($0) }
    }

    public static func reportedCostEmpty(_ provider: Provider) -> EmptyCopy? {
        guard provider.costs.reported == nil else { return nil }
        if reportsNoCost(provider) {
            return EmptyCopy(symbol: "dollarsign.circle", title: "Usage cost isn't reported by this provider",
                             detail: provider.costs.reportedNote ?? "", action: nil)
        }
        let detail = isNotCollecting(provider) ? "Tracking is off, so no cost is read."
            : (provider.costs.reportedNote ?? "No usage cost has been read yet.")
        return EmptyCopy(symbol: "dollarsign.circle", title: "No cost reading yet", detail: detail, action: nil)
    }

    public static func subscriptionEmpty(_ provider: Provider) -> EmptyCopy? {
        guard provider.costs.subscription == nil else { return nil }
        return EmptyCopy(symbol: "dollarsign", title: "Add subscription price",
                         detail: "Entered by you; never counted as usage cost.", action: .providerSettings)
    }
}
