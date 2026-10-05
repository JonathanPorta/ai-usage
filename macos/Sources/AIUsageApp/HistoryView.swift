import AIUsageCore
import AIUsageDesign
import SwiftUI

/// History: a separate, resizable window reading the same AppStore as the
/// popover. Its selections (provider, metric, window, range) are local state,
/// so they survive every store update, including Collect now.
struct HistoryView: View {
    static let windowId = "history"

    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    @State private var providerId: String?
    @State private var metric: ProviderDetailView.Metric = .tokens
    @State private var tokenRange = 30
    @State private var windowId: String?
    @State private var quotaRange: String?

    init(providerId: String? = nil, metric: ProviderDetailView.Metric = .tokens) {
        _providerId = State(initialValue: providerId)
        _metric = State(initialValue: metric)
    }

    var body: some View {
        TimelineView(.everyMinute) { _ in content }
    }

    private var content: some View {
        HStack(spacing: 0) {
            sidebar
                .frame(width: 210)
                .background(T.Color.background.color)
            Divider()
            ScrollView {
                if let report = store.report, let provider = selected(report) {
                    main(report: report, provider: provider)
                        .padding(T.Space.s5)
                } else {
                    Text(store.report == nil ? "Reading collector data…" : "Pick a provider.")
                        .font(.auBody).foregroundStyle(T.Color.muted.color)
                        .frame(maxWidth: .infinity, minHeight: 300)
                }
            }
            .background(T.Color.background.color)
        }
        .onAppear(perform: applyRequest)
        .onChange(of: navigation.historyRequest) { _, _ in applyRequest() }
    }

    private func applyRequest() {
        if let id = navigation.historyRequest?.providerId {
            providerId = id
            metric = .tokens
        }
    }

    private func selected(_ report: Report) -> Provider? {
        report.provider(providerId ?? "") ?? report.providers.first { $0.usage.status != .none } ?? report.providers.first
    }

    // MARK: Sidebar

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("Providers").padding(.horizontal, T.Space.s3).padding(.top, T.Space.s4)
            if let report = store.report {
                let current = selected(report)?.id
                ForEach(report.providers) { provider in
                    Button {
                        providerId = provider.id
                        windowId = nil
                        quotaRange = nil
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(provider.name).font(.auBody)
                            Text(sidebarCaption(provider)).font(.auCaption)
                                .foregroundStyle(provider.id == current ? T.Color.text.color : T.Color.muted.color)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, T.Space.s2).padding(.vertical, 6)
                        .background(provider.id == current ? T.Color.accentBackground.color : .clear,
                                    in: RoundedRectangle(cornerRadius: T.Radius.small))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .padding(.horizontal, T.Space.s2)
                    .accessibilityAddTraits(provider.id == current ? .isSelected : [])
                }
            }
            Text("One provider at a time: token counts and quota percentages from different tools aren’t comparable.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color)
                .padding(T.Space.s3)
            Spacer()
        }
    }

    private func sidebarCaption(_ provider: Provider) -> String {
        if let setup = Presentation.setupText(provider), provider.days.allSatisfy({ !$0.hasValue }) {
            return setup.count > 28 ? (provider.setup == .notDetected ? "Not found" : "Not set up") : setup
        }
        let days = Presentation.recentDays(provider, count: 30)
        return "\(Format.tokens(Presentation.sum(days))) tokens · 30 d"
    }

    // MARK: Main

    @ViewBuilder
    private func main(report: Report, provider: Provider) -> some View {
        let now = store.now
        VStack(alignment: .leading, spacing: T.Space.s4) {
            HStack(alignment: .center, spacing: T.Space.s3) {
                Text(provider.name).font(.auTitle).accessibilityAddTraits(.isHeader)
                Spacer()
                if let last = report.collection.lastAttemptAt {
                    Text("Last check \(Format.moment(last, now: now))").font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
                if !provider.activeWindows.isEmpty {
                    Segmented("Metric", selection: $metric, options: [(.tokens, "Tokens"), (.quota, "Quota")])
                }
                if metric == .tokens || provider.activeWindows.isEmpty {
                    Segmented("Range", selection: $tokenRange, options: [(7, "7 days"), (30, "30 days"), (90, "90 days")])
                }
            }
            if metric == .quota, let window = window(provider) {
                QuotaHistory(provider: provider, window: window, now: now,
                             windowId: $windowId, range: $quotaRange,
                             gap: Double(report.thresholds.staleAfterSeconds) * 1.5)
            } else {
                TokenHistory(provider: provider, days: Presentation.recentDays(provider, count: tokenRange),
                             today: report.today)
            }
            VStack(alignment: .leading, spacing: T.Space.s2) {
                SectionLabel("Accounting")
                Panel { AccountingView(provider: provider, now: now) }
            }
        }
        .id(provider.id)
    }

    private func window(_ provider: Provider) -> QuotaWindow? {
        provider.activeWindows.first { $0.id == windowId } ?? provider.activeWindows.first
    }
}

// MARK: - Tokens

struct TokenHistory: View {
    let provider: Provider
    let days: [Day]
    let today: String

    var body: some View {
        let measured = days.filter { $0.hasValue }
        let complete = measured.filter { $0.state != .partial }
        let total = Presentation.sum(days)
        let busiest = complete.max { ($0.total ?? 0) < ($1.total ?? 0) }
        let notCollected = days.filter { $0.state == .notCollected }.count
        VStack(alignment: .leading, spacing: T.Space.s3) {
            Text("Daily tokens · last \(days.count) days").font(.auLead)
            Text(description(notCollected: notCollected))
                .font(.auBody).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: T.Space.s2)], spacing: T.Space.s2) {
                StatTile(value: Format.tokens(total), caption: "\(Presentation.seriesLabel(provider).capitalizedFirst) · \(days.count) days (today so far)")
                StatTile(value: complete.isEmpty ? "—" : Format.tokens(Presentation.sum(complete) / Double(complete.count)),
                         caption: "Average per complete measured day")
                StatTile(value: busiest.map { Format.tokens($0.total) } ?? "—",
                         caption: busiest.map { "Busiest day · \(Format.longDay($0.date))" } ?? "Busiest day")
                StatTile(value: "\(measured.count) of \(days.count - notCollected)", caption: "Days measured")
                if let cache = cacheTotal {
                    StatTile(value: Format.tokens(cache), caption: "Cache reads · \(days.count) days (separate)")
                }
            }
            Panel {
                VStack(alignment: .leading, spacing: T.Space.s2) {
                    DayBarChart(provider: provider, days: days, style: .full, today: today)
                    DayChartLegend(split: provider.usage.hasSplit)
                }
            }
            SectionLabel("Daily detail")
            DailyTable(provider: provider, days: days.reversed(), today: today)
        }
    }

    private var cacheTotal: Double? {
        let values = days.compactMap(\.cacheRead)
        return values.isEmpty ? nil : values.reduce(0, +)
    }

    private func description(notCollected: Int) -> String {
        var text = provider.usage.hasSplit
            ? "Input and output tokens per calendar day to \(Format.longDay(today)). Cache tokens are counted separately."
            : "\(provider.name) reports one daily total (no input/output split). Daily totals the provider revises use the latest value."
        if notCollected > 0, let start = provider.coverageStart {
            text += " Collection for \(provider.name) began on \(Format.dayLabel(start)); \(notCollected) earlier days are shaded “Not collected”."
        }
        return text
    }
}

struct DailyTable: View {
    let provider: Provider
    let days: [Day]
    let today: String

    var body: some View {
        Panel(padding: 0) {
            VStack(spacing: 0) {
                row(["Date", provider.usage.hasSplit ? "Input" : "", provider.usage.hasSplit ? "Output" : "", "Total", "Cache reads", "State"], header: true)
                ForEach(days.filter { $0.state != .notCollected }) { day in
                    Divider()
                    row([
                        Format.longDay(day.date),
                        provider.usage.hasSplit ? Format.tokens(day.input) : "",
                        provider.usage.hasSplit ? Format.tokens(day.output) : "",
                        day.hasValue ? Format.tokens(day.total) : "—",
                        Format.tokens(day.cacheRead),
                        stateText(day),
                    ], header: false)
                }
            }
        }
    }

    private func stateText(_ day: Day) -> String {
        switch day.state {
        case .measured: return day.incompleteEvents > 0 ? "Measured · \(day.incompleteEvents) incomplete" : "Measured"
        case .zero: return "Measured zero"
        case .missing: return "Missing (not zero)"
        case .partial:
            if day.date == today { return "Today so far" }
            return day.asOf.map { "Partial (read until \(Format.clock($0)))" } ?? "Partial"
        case .notCollected: return "Not collected"
        }
    }

    private func row(_ cells: [String], header: Bool) -> some View {
        HStack {
            ForEach(Array(cells.enumerated()), id: \.offset) { index, cell in
                Text(cell)
                    .frame(maxWidth: .infinity, alignment: index == 0 || index == 5 ? .leading : .trailing)
            }
        }
        .font(header ? .auCaption.weight(.semibold) : .auCaption.monospacedDigit())
        .foregroundStyle(header ? T.Color.muted.color : T.Color.text.color)
        .padding(.horizontal, T.Space.s3)
        .padding(.vertical, 5)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Quota

struct QuotaHistory: View {
    let provider: Provider
    let window: QuotaWindow
    let now: Date
    @Binding var windowId: String?
    @Binding var range: String?
    let gap: TimeInterval

    private var selectedRange: String {
        if let range, window.ranges.contains(range) { return range }
        return window.ranges.contains("7d") ? "7d" : (window.ranges.first ?? "7d")
    }

    var body: some View {
        let seconds = RangeText.seconds(selectedRange)
        let readings = window.readings.filter { $0.t >= now.addingTimeInterval(-seconds) }
        let resets = window.resets.filter { $0 >= now.addingTimeInterval(-seconds) && $0 <= now }
        VStack(alignment: .leading, spacing: T.Space.s3) {
            HStack {
                Text("\(window.label) · last \(RangeText.label(selectedRange))").font(.auLead)
                Spacer()
                if provider.activeWindows.count > 1 {
                    Picker("Window", selection: Binding(get: { window.id }, set: { windowId = $0; range = nil })) {
                        ForEach(provider.activeWindows) { Text($0.label).tag($0.id) }
                    }
                    .labelsHidden().controlSize(.small).fixedSize()
                }
                Segmented("Quota range", selection: Binding(get: { selectedRange }, set: { range = $0 }),
                          options: window.ranges.map { ($0, RangeText.label($0)) })
            }
            Text("Percentage \(window.basis == .remaining ? "remaining" : "used") in \(provider.name)’s \(window.label.lowercased()). Readings are snapshots: they are plotted, never added up\(window.derived ? "; remaining is derived from the reported % used" : "").")
                .font(.auBody).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: T.Space.s2)], spacing: T.Space.s2) {
                StatTile(value: Presentation.quotaValue(window), caption: "\(Presentation.quotaBasis(window).capitalizedFirst) · \(window.status == .stale ? "stale" : "current")")
                StatTile(value: "\(readings.count)", caption: "Readings in range")
                StatTile(value: "\(resets.count)", caption: "Resets in range")
                StatTile(value: Format.moment(window.measuredAt, now: now), caption: "Latest reading")
            }
            Panel { QuotaLineChart(window: window, rangeSeconds: seconds, now: now, gapSeconds: gap) }
            SectionLabel("Readings")
            Panel(padding: 0) {
                VStack(spacing: 0) {
                    ForEach(Array(readings.suffix(40).reversed().enumerated()), id: \.offset) { index, reading in
                        if index > 0 { Divider() }
                        HStack {
                            Text(Format.moment(reading.t, now: now)).frame(maxWidth: .infinity, alignment: .leading)
                            Text("\(Format.percent(reading.percent)) \(Presentation.quotaBasis(window))")
                                .frame(maxWidth: .infinity, alignment: .trailing)
                            Text(mark(reading, isLatest: index == 0)).frame(maxWidth: .infinity, alignment: .leading)
                                .foregroundStyle(T.Color.muted.color)
                        }
                        .font(.auCaption.monospacedDigit())
                        .padding(.horizontal, T.Space.s3).padding(.vertical, 5)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
    }

    private func mark(_ reading: QuotaWindow.Reading, isLatest: Bool) -> String {
        var marks: [String] = []
        let remaining = window.basis == .remaining ? reading.percent : 100 - reading.percent
        if isLatest && window.status == .stale { marks.append("Latest reading · stale") }
        if remaining <= 0 { marks.append("Limit reached") }
        if let index = window.readings.firstIndex(of: reading), index > 0 {
            let previous = window.readings[index - 1]
            if window.resets.contains(where: { $0 > previous.t && $0 <= reading.t }) { marks.append("After a reset") }
        }
        return marks.joined(separator: " · ")
    }
}

extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
