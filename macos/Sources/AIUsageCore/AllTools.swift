import Foundation

/// History's All tools page (phase 4, tasks/phase-4/prd-all-view.md). The reporter combines
/// each type's accounts per day (`report.groups`); this model only derives what the page
/// shows from it: figures by rules F1–F4, the series with the overflow rule, the chart's
/// bars and every account's breakdown. Views draw it and contain no rules.
public struct AllToolsPage: Equatable, Sendable {
    public var range: Int
    public var sections: [AllToolsSection]
    /// Tools whose every account is off, or nil when there are none.
    public var notIncluded: String?

    public static let about = "Each tool's accounts are added together; tools are never added to each other. "
        + "The figures cover the accounts tracked now: a turned-off account's history is left out. "
        + "Quota limits are per account, on each account's own page. Missing readings are never counted as zero, "
        + "and a figure that may be low reads “at least”."

    /// Nil when the report has no groups (an older reporter): All tools is then hidden.
    public init?(_ report: Report, range: Int) {
        guard let groups = report.groups else { return nil }
        self.range = range
        let providers = Dictionary(report.providers.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        sections = groups.filter { !$0.members.isEmpty }
            .map { AllToolsSection($0, providers: providers, range: range, today: report.today) }
        let off = groups.filter { $0.members.isEmpty && !$0.excluded.isEmpty }.map(\.name)
        notIncluded = off.isEmpty ? nil : "Not included: \(AllToolsText.list(off)), tracking off"
    }
}

public struct AllToolsFigure: Equatable, Sendable {
    /// Nil when nothing is known: shown as "—", never 0.
    public var value: Double?
    /// The figure may be low: shown as "at least".
    public var qualified: Bool

    public var text: String {
        guard let value else { return "—" }
        return qualified ? "at least \(Format.tokens(value))" : Format.tokens(value)
    }
}

/// One account series in a section's chart and legend.
public struct AllToolsSeries: Equatable, Sendable {
    /// 0 to 3: the account token (`chart.account1` to `account4`).
    public var index: Int
    public var label: String
    public var members: [String]
}

public struct AllToolsSegment: Equatable, Sendable {
    public var series: Int
    public var value: Double
}

/// One day of a section's chart.
public struct AllToolsBar: Equatable, Sendable {
    public var date: String
    public var state: GroupDay.State
    /// The known total, nil when nothing is known.
    public var total: Double?
    /// Known values per series, bottom to top; zero values are left out.
    public var segments: [AllToolsSegment]
    /// An account had no reading while another had one (an incomplete day). The marker
    /// sits below the baseline and carries no amount.
    public var marker: Bool
    public var missing: [String]
    public var readout: String
}

public struct AllToolsRow: Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var series: Int
    public var range: AllToolsFigure
    public var today: AllToolsFigure
    public var status: String
}

public struct AllToolsSection: Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var title: String
    public var range: AllToolsFigure
    public var rangeCaption: String
    public var today: AllToolsFigure
    public var todayCaption: String
    public var series: [AllToolsSeries]
    public var bars: [AllToolsBar]
    public var rows: [AllToolsRow]
    public var excluded: String?
    /// Whether any day in the range has a known value; without one the chart is left out.
    public var hasReadings: Bool
    public var caption: String

    init(_ group: ToolGroup, providers: [String: Provider], range: Int, today: String) {
        let days = Array(group.days.suffix(range))
        let names = Dictionary(group.members.map { ($0, providers[$0]?.name ?? $0) }, uniquingKeysWith: { a, _ in a })
        let series = AllToolsSeries.assign(group.members, names: names)
        let seriesOf = Dictionary(series.flatMap { s in s.members.map { ($0, s.index) } }, uniquingKeysWith: { a, _ in a })
        id = group.type
        name = group.name
        let count = group.members.count
        title = "\(group.name) · \(count == 1 ? "1 account" : "\(count) accounts") tracked now"
        self.series = series
        let readings = days.map(AllToolsReading.init)
        self.range = AllToolsRules.rangeFigure(readings, today: today)
        rangeCaption = self.range.value == nil
            ? "No readings in \(range) days"
            : "Total · \(range) days, including today so far"
        let todayDay = days.last { $0.date == today }
        self.today = AllToolsRules.todayFigure(todayDay.map(AllToolsReading.init))
        todayCaption = self.today.value == nil ? "No reading today" : (self.today.qualified ? "Today" : "Today so far")
        bars = days.map { AllToolsBar($0, names: names, seriesOf: seriesOf, today: today) }
        rows = group.members.map { id in
            AllToolsRow(provider: providers[id], id: id, name: names[id] ?? id, series: seriesOf[id] ?? 0,
                        range: range, today: today)
        }
        let excludedNames = group.excluded.map { providers[$0.id]?.name ?? $0.id }
        excluded = excludedNames.isEmpty ? nil : "Not included: \(AllToolsText.list(excludedNames)), tracking off"
        hasReadings = days.contains { $0.total != nil }
        caption = "\(range) days · \(group.name)" + (count > 1 ? " · \(count) accounts stacked" : "")
    }
}

extension AllToolsSeries {
    /// Up to four accounts are their own series. From five on, the first three are, and the
    /// rest share the fourth as "Other accounts" (every one still named in the breakdown).
    public static func assign(_ members: [String], names: [String: String]) -> [AllToolsSeries] {
        if members.count <= 4 {
            return members.enumerated().map { AllToolsSeries(index: $0.offset, label: names[$0.element] ?? $0.element,
                                                             members: [$0.element]) }
        }
        let own = members.prefix(3).enumerated().map {
            AllToolsSeries(index: $0.offset, label: names[$0.element] ?? $0.element, members: [$0.element])
        }
        let others = Array(members.dropFirst(3))
        return own + [AllToolsSeries(index: 3, label: "Other accounts (\(others.count))", members: others)]
    }
}

/// A day's figure inputs, for a group day or for one account's own day.
struct AllToolsReading {
    var date: String
    var state: GroupDay.State
    var total: Double?
    var incompleteEvents: Int

    init(_ day: GroupDay) {
        (date, state, total, incompleteEvents) = (day.date, day.state, day.total, day.incompleteEvents)
    }

    init(_ day: Day) {
        date = day.date
        state = GroupDay.State(rawValue: day.state.rawValue) ?? .missing
        total = day.state == .missing || day.state == .notCollected ? nil : day.total
        incompleteEvents = day.incompleteEvents
    }
}

/// Rules F1 to F3 of the PRD, each figure on its own.
enum AllToolsRules {
    /// F1: a day's figure may be low when an account is missing, the day was cut short, or
    /// turns reported incomplete usage. Today being partial is "so far", not low.
    static func dayQualified(_ day: AllToolsReading, today: String) -> Bool {
        day.state == .incomplete || (day.state == .partial && day.date != today) || day.incompleteEvents > 0
    }

    /// F2: the sum of known days; "at least" when any day in the range may be low or has no
    /// reading after collection began. Today so far and days before collection don't count.
    static func rangeFigure(_ days: [AllToolsReading], today: String) -> AllToolsFigure {
        let known = days.compactMap(\.total)
        guard !known.isEmpty else { return AllToolsFigure(value: nil, qualified: false) }
        let qualified = days.contains { $0.state == .missing || dayQualified($0, today: today) }
        return AllToolsFigure(value: known.reduce(0, +), qualified: qualified)
    }

    /// F3: today's day alone. An earlier incomplete day never changes it.
    static func todayFigure(_ day: AllToolsReading?) -> AllToolsFigure {
        guard let day, let total = day.total else { return AllToolsFigure(value: nil, qualified: false) }
        return AllToolsFigure(value: total, qualified: day.state == .incomplete || day.incompleteEvents > 0)
    }
}

extension AllToolsBar {
    init(_ day: GroupDay, names: [String: String], seriesOf: [String: Int], today: String) {
        date = day.date
        state = day.state
        total = day.total
        var sums: [Int: Double] = [:]
        for part in day.parts {
            if let value = part.total, value > 0 { sums[seriesOf[part.id] ?? 0, default: 0] += value }
        }
        segments = sums.keys.sorted().map { AllToolsSegment(series: $0, value: sums[$0] ?? 0) }
        missing = day.parts.filter { $0.state == .missing }.map { names[$0.id] ?? $0.id }
        marker = day.state == .incomplete
        readout = AllToolsText.readout(day, names: names, today: today)
    }

    /// Segment heights for a plot, exactly the scaled known values: they add up to the
    /// scaled total, and a missing account adds nothing.
    public func heights(scale: Double) -> [Double] { segments.map { $0.value * scale } }

    /// The plot's scale: the range's known peak (with headroom) fills `plotHeight`.
    public static func scale(_ bars: [AllToolsBar], plotHeight: Double) -> Double {
        let peak = bars.compactMap(\.total).max() ?? 0
        return peak > 0 ? plotHeight / (peak * 1.08) : 0
    }
}

extension AllToolsRow {
    init(provider: Provider?, id: String, name: String, series: Int, range: Int, today: String) {
        let days = Array((provider?.days ?? []).suffix(range))
        let readings = days.map(AllToolsReading.init)
        self.id = id
        self.name = name
        self.series = series
        self.range = AllToolsRules.rangeFigure(readings, today: today)
        self.today = AllToolsRules.todayFigure(readings.last { $0.date == today })
        status = AllToolsText.status(days, coverageStart: provider?.coverageStart, today: today)
    }
}

/// The page's words: readouts (F1 and F4) and statuses.
public enum AllToolsText {
    static func list(_ names: [String]) -> String {
        switch names.count {
        case 0: return ""
        case 1: return names[0]
        default: return names.dropLast().joined(separator: ", ") + " and " + names[names.count - 1]
        }
    }

    /// F1 and F4: the day's figure, then every account's part by name, each with its own
    /// state and, for a partial day, its own time. There is never one combined time.
    public static func readout(_ day: GroupDay, names: [String: String], today: String) -> String {
        let date = day.date == today ? "Today" : Format.longDay(day.date)
        let figure = AllToolsFigure(value: day.total,
                                    qualified: AllToolsRules.dayQualified(AllToolsReading(day), today: today))
        let head: String
        switch day.state {
        case .notCollected: return "\(date): not collected"
        case .missing: head = "\(date): no data — missing reading, not zero"
        case .zero: head = "\(date): 0 tokens — measured, no usage"
        case .partial where day.date == today: head = "\(date) so far: \(figure.text) tokens"
        case .partial: head = "\(date): \(figure.text) tokens, day incomplete"
        case .incomplete, .measured: head = "\(date): \(figure.text) tokens"
        }
        // One account's part repeats the head unless it adds its own time or incomplete turns.
        let lone = day.parts.count == 1 && day.parts[0].state != .partial && day.parts[0].incompleteEvents == 0
        let parts = lone ? [] : day.parts.map { part($0, name: names[$0.id] ?? $0.id, today: day.date == today) }
        return parts.isEmpty ? head : "\(head): \(parts.joined(separator: ", "))"
    }

    static func part(_ part: GroupDay.Part, name: String, today: Bool) -> String {
        var text: String
        switch part.state {
        case .missing: text = "\(name) missing reading"
        case .notCollected: text = "\(name) not collected"
        case .zero: text = "\(name) 0"
        case .measured: text = "\(name) \(Format.tokens(part.total))"
        case .partial:
            let time = part.asOf.map { today ? "as of \(Format.clock($0))" : "read until \(Format.clock($0))" }
            text = "\(name) \(Format.tokens(part.total)) \(time ?? "time unknown")"
        }
        if part.incompleteEvents > 0 {
            text += " (\(part.incompleteEvents) \(part.incompleteEvents == 1 ? "turn" : "turns") reported incomplete usage)"
        }
        return text
    }

    /// What qualifies one account's figures over the range, or "Complete".
    static func status(_ days: [Day], coverageStart: String?, today: String) -> String {
        guard days.contains(where: \.hasValue) else { return "No readings in \(days.count) days" }
        var notes: [String] = []
        let missing = days.filter { $0.state == .missing }.count
        if missing > 0 { notes.append("\(missing) \(missing == 1 ? "day" : "days") missing") }
        let cut = days.filter { $0.state == .partial && $0.date != today }.count
        if cut > 0 { notes.append("\(cut) \(cut == 1 ? "day" : "days") incomplete") }
        let events = days.reduce(0) { $0 + $1.incompleteEvents }
        if events > 0 { notes.append("\(events) incomplete \(events == 1 ? "turn" : "turns")") }
        if let start = coverageStart, let first = days.first?.date, start > first {
            notes.append("collected from \(Format.dayLabel(start))")
        }
        return notes.isEmpty ? "Complete" : notes.joined(separator: " · ")
    }
}

/// What History shows: All tools or one provider. All tools needs the report's groups.
public enum HistorySelection: Equatable, Sendable {
    case allTools
    case provider(String)

    /// The selection the window can show: All tools falls back to a provider when the
    /// report has no groups.
    public func resolved(in report: Report) -> HistorySelection {
        if self == .allTools && report.groups == nil {
            let first = report.providers.first { $0.usage.status != .none } ?? report.providers.first
            return .provider(first?.id ?? "")
        }
        return self
    }
}
