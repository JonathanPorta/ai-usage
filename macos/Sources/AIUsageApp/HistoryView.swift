import AIUsageCore
import AIUsageDesign
import SwiftUI

/// History: a separate, resizable window reading the same AppStore as the
/// popover. Its selections (provider, metric, window, ranges) are local state,
/// so they survive every store update, including Collect now.
///
/// Every provider and mode shares one layout, so switching never moves controls
/// or the chart:
/// 1. provider name | last check · Tokens/Quota (always present)
/// 2. compact chart title and units | quota window · range (anchored right)
/// 3. four equal summary cards (one row, or 2 × 2 below the shared breakpoint)
/// 4. chart
/// 5. detail table and secondary information
struct HistoryView: View {
    static let windowId = "history"
    /// Main-column width below which the header and cards switch to their compact
    /// arrangement. One breakpoint for every provider and mode.
    static let compactBreakpoint: CGFloat = 600

    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    @State private var providerId: String?
    @State private var metric: ProviderDetailView.Metric = .tokens
    /// Each mode keeps its own range when switching away and back.
    @State private var tokenRange = 30
    @State private var quotaRange: String?
    @State private var windowId: String?
    /// macOS gives the first control keyboard focus when the window opens; its
    /// ring shows only once the keyboard is used (as in the popover).
    @State private var focusRingsVisible = false

    init(providerId: String? = nil, metric: ProviderDetailView.Metric = .tokens, tokenRange: Int = 30) {
        _providerId = State(initialValue: providerId)
        _metric = State(initialValue: metric)
        _tokenRange = State(initialValue: tokenRange)
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
            GeometryReader { proxy in
                let compact = proxy.size.width - 2 * T.Space.s5 < Self.compactBreakpoint
                ScrollView {
                    if let report = store.report, let provider = selected(report) {
                        main(report: report, provider: provider, compact: compact)
                            .padding(T.Space.s5)
                    } else {
                        Text(store.report == nil ? "Reading collector data…" : "Pick a provider.")
                            .font(.auBody).foregroundStyle(T.Color.muted.color)
                            .frame(maxWidth: .infinity, minHeight: 300)
                    }
                }
            }
            .background(T.Color.background.color)
        }
        .onAppear(perform: applyRequest)
        .onChange(of: navigation.historyRequest) { _, _ in applyRequest() }
        .focusEffectDisabled(!focusRingsVisible)
        .environment(\.focusRingsVisible, focusRingsVisible)
        .onKeyPress(phases: .down) { _ in
            focusRingsVisible = true
            return .ignored
        }
        .background(MouseDownObserver { focusRingsVisible = false })
        .background(PanelShownObserver { focusRingsVisible = false })
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

    /// The sidebar is a fixed 30-day token overview, independent of the chart's
    /// range and mode, and says so.
    private var sidebar: some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            VStack(alignment: .leading, spacing: 2) {
                SectionLabel("Providers")
                Text("30-day overview").font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            .padding(.horizontal, T.Space.s3).padding(.top, T.Space.s4)
            if let report = store.report {
                let current = selected(report)?.id
                ForEach(report.providers) { provider in
                    Button {
                        providerId = provider.id
                        windowId = nil
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(provider.name).font(.auBody)
                            Text(Presentation.historySidebarCaption(provider)).font(.auCaption)
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
                    .accessibilityHint("30-day token overview")
                }
            }
            Spacer()
        }
    }

    // MARK: Main

    @ViewBuilder
    private func main(report: Report, provider: Provider, compact: Bool) -> some View {
        let now = store.now
        let quota = Presentation.quotaSupport(provider)
        let mode: ProviderDetailView.Metric = quota.supported ? metric : .tokens
        let window = provider.activeWindows.first { $0.id == windowId } ?? provider.activeWindows.first
        let days = Presentation.recentDays(provider, count: tokenRange)
        VStack(alignment: .leading, spacing: T.Space.s4) {
            // Row 1: provider | last check · mode.
            HStack(alignment: .center, spacing: T.Space.s3) {
                Text(provider.name).font(.auTitle).lineLimit(1).accessibilityAddTraits(.isHeader)
                Spacer(minLength: T.Space.s2)
                if let last = report.collection.lastAttemptAt {
                    Text("Last check \(Format.moment(last, now: now))").font(.auCaption)
                        .foregroundStyle(T.Color.muted.color).lineLimit(1)
                }
                ModeSwitch(selection: $metric, effective: mode, quotaReason: quota.reason)
            }
            .frame(height: 30)

            // Row 2: chart title | quota window · range.
            if mode == .quota, let window {
                let range = quotaRangeKey(window)
                ControlRow(compact: compact,
                           title: "\(window.label) · \(RangeText.label(range))",
                           subtitle: quotaSubtitle(window, now: now)) {
                    if provider.activeWindows.count > 1 {
                        Picker("Quota window", selection: Binding(get: { window.id }, set: { windowId = $0 })) {
                            ForEach(provider.activeWindows) { Text($0.label).tag($0.id) }
                        }
                        .labelsHidden().controlSize(.small).fixedSize()
                    }
                    Segmented("Quota range", selection: Binding(get: { range }, set: { quotaRange = $0 }),
                              options: window.ranges.map { ($0, RangeText.label($0)) })
                }
                QuotaHistory(provider: provider, window: window, range: range, now: now, compact: compact,
                             gap: Double(report.thresholds.staleAfterSeconds) * 1.5)
            } else {
                let availability = Presentation.historyAvailability(provider, days: days)
                ControlRow(compact: compact,
                           title: "Daily tokens · \(tokenRange) days",
                           subtitle: tokenSubtitle(provider, today: report.today)) {
                    Segmented("Range", selection: $tokenRange, options: [(7, "7 days"), (30, "30 days"), (90, "90 days")])
                        .disabled(availability != .data && availability != .noneInRange)
                }
                if availability == .data {
                    TokenHistory(provider: provider, days: days, today: report.today, compact: compact)
                    SetupNotice(provider: provider)
                } else {
                    HistoryEmptyState(provider: provider, availability: availability, range: tokenRange,
                                      showAll: { tokenRange = 90 })
                }
            }
            if Presentation.historyAvailability(provider, days: provider.days) == .data {
                VStack(alignment: .leading, spacing: T.Space.s2) {
                    SectionLabel("Accounting")
                    Panel { AccountingView(provider: provider, now: now) }
                }
            }
        }
    }

    private func quotaRangeKey(_ window: QuotaWindow) -> String {
        if let quotaRange, window.ranges.contains(quotaRange) { return quotaRange }
        return window.ranges.contains("7d") ? "7d" : (window.ranges.first ?? "7d")
    }

    /// Units and freshness only; how the numbers are computed is under "About
    /// these numbers" below the chart.
    private func tokenSubtitle(_ provider: Provider, today: String) -> String {
        var text = "\(Presentation.seriesLabel(provider).capitalizedFirst) per day, to \(Format.longDay(today))"
        if provider.usage.status == .stale { text += " · stale" }
        return text
    }

    private func quotaSubtitle(_ window: QuotaWindow, now: Date) -> String {
        "% \(Presentation.quotaBasis(window)) · \(window.status == .stale ? "stale" : "current") · latest \(Format.moment(window.measuredAt, now: now))"
    }
}

// MARK: - Shared layout pieces

/// Row 2: a one-line title and a one-line subtitle on the left, controls anchored
/// right. Fixed height, so text never changes the layout; in the compact
/// arrangement the controls move to their own right-aligned line.
private struct ControlRow<Controls: View>: View {
    let compact: Bool
    let title: String
    let subtitle: String
    @ViewBuilder let controls: Controls

    var body: some View {
        let text = VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.auLead).lineLimit(1)
            Text(subtitle).font(.auCaption).foregroundStyle(T.Color.muted.color).lineLimit(1).truncationMode(.tail)
        }
        .accessibilityElement(children: .combine)
        Group {
            if compact {
                VStack(alignment: .leading, spacing: T.Space.s2) {
                    text
                    HStack(spacing: T.Space.s2) { Spacer(minLength: 0); controls }
                }
                .frame(height: 72, alignment: .top)
            } else {
                HStack(alignment: .center, spacing: T.Space.s3) {
                    text
                    Spacer(minLength: T.Space.s2)
                    controls
                }
                .frame(height: 40)
            }
        }
    }
}

/// Tokens / Quota, always in the same place. Quota is disabled, with the reason
/// as help and accessibility hint, when the provider genuinely reports none.
private struct ModeSwitch: View {
    @Binding var selection: ProviderDetailView.Metric
    let effective: ProviderDetailView.Metric
    let quotaReason: String?

    var body: some View {
        HStack(spacing: 2) {
            segment("Tokens", .tokens, enabled: true)
            segment("Quota", .quota, enabled: quotaReason == nil)
        }
        .padding(2)
        .background(T.Color.cardBackground.color, in: RoundedRectangle(cornerRadius: T.Radius.small))
        .overlay(RoundedRectangle(cornerRadius: T.Radius.small).strokeBorder(T.Color.cardBorder.color, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Metric")
    }

    private func segment(_ title: String, _ value: ProviderDetailView.Metric, enabled: Bool) -> some View {
        let isOn = effective == value
        return Button { selection = value } label: {
            // Sized by the semibold title, so selecting a segment never
            // changes its width, and never truncated in a narrow window.
            ZStack {
                Text(title).font(.auCaption.weight(.semibold)).hidden()
                Text(title).font(.auCaption.weight(isOn ? .semibold : .regular))
                    .foregroundStyle(enabled ? T.Color.text.color : T.Color.muted.color.opacity(0.6))
            }
                .lineLimit(1)
                .fixedSize()
                .padding(.horizontal, T.Space.s2).padding(.vertical, 3)
                .frame(minWidth: 52)
                .background(isOn ? T.Color.accentBackground.color : .clear, in: RoundedRectangle(cornerRadius: 4))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .help(enabled ? "" : (quotaReason ?? ""))
        .accessibilityAddTraits(isOn ? .isSelected : [])
        .accessibilityHint(enabled ? "" : (quotaReason ?? ""))
    }
}

/// Four equal summary cards: same width, height, padding and alignment. The
/// caption always reserves two lines, so a longer label never makes one card
/// taller or re-centres its value.
private struct SummaryCards: View {
    let compact: Bool
    let cards: [(value: String, caption: String)]

    var body: some View {
        let columns = Array(repeating: GridItem(.flexible(), spacing: T.Space.s2), count: compact ? 2 : 4)
        LazyVGrid(columns: columns, alignment: .leading, spacing: T.Space.s2) {
            ForEach(Array(cards.enumerated()), id: \.offset) { _, card in
                VStack(alignment: .leading, spacing: 2) {
                    Text(card.value).font(.system(size: 17, weight: .medium).monospacedDigit())
                        .foregroundStyle(T.Color.text.color).lineLimit(1).minimumScaleFactor(0.8)
                    Text(card.caption).font(.auCaption).foregroundStyle(T.Color.muted.color)
                        .lineLimit(2, reservesSpace: true)
                }
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .padding(T.Space.s3)
                .background(T.Color.cardBackground.color, in: RoundedRectangle(cornerRadius: T.Radius.medium))
                .overlay(RoundedRectangle(cornerRadius: T.Radius.medium).strokeBorder(T.Color.cardBorder.color, lineWidth: 1))
                .accessibilityElement(children: .combine)
            }
        }
    }
}

/// Secondary explanation, collapsed by default, below the chart.
private struct AboutNumbers: View {
    let text: String
    @FocusState private var focused: Bool
    @Environment(\.focusRingsVisible) private var focusRingsVisible

    var body: some View {
        DisclosureGroup {
            Text(text).font(.auCaption).foregroundStyle(T.Color.muted.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, T.Space.s1)
        } label: {
            // The native ring is drawn in the triangle's gutter, over the
            // sidebar divider; draw it around the label instead.
            Text("About these numbers").font(.auCaption)
                .overlay {
                    if focused && focusRingsVisible {
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(Color(nsColor: .keyboardFocusIndicatorColor), lineWidth: 3)
                            .padding(.horizontal, -4).padding(.vertical, -2)
                    }
                }
        }
        .focusEffectDisabled()
        .focused($focused)
    }
}

// MARK: - Tokens

struct TokenHistory: View {
    let provider: Provider
    let days: [Day]
    let today: String
    let compact: Bool

    var body: some View {
        let measured = days.filter { $0.hasValue }
        let complete = measured.filter { $0.state != .partial }
        let total = Presentation.sum(days)
        let busiest = complete.max { ($0.total ?? 0) < ($1.total ?? 0) }
        let notCollected = days.filter { $0.state == .notCollected }.count
        VStack(alignment: .leading, spacing: T.Space.s3) {
            SummaryCards(compact: compact, cards: [
                (Format.tokens(total), "Total tokens · \(days.count) days"),
                (complete.isEmpty ? "—" : Format.tokens(Presentation.sum(complete) / Double(complete.count)),
                 "Average per complete measured day"),
                (busiest.map { Format.tokens($0.total) } ?? "—",
                 busiest.map { "Busiest day · \(Format.longDay($0.date))" } ?? "Busiest day"),
                ("\(measured.count) of \(days.count - notCollected)", "Days measured"),
            ])
            Panel {
                VStack(alignment: .leading, spacing: T.Space.s2) {
                    DayBarChart(provider: provider, days: days, style: .full, today: today)
                    DayChartLegend(split: provider.usage.hasSplit)
                }
            }
            if let cache = cacheTotal {
                Text("Cache reads · \(days.count) days: \(Format.tokens(cache)), counted separately from input and output")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .accessibilityLabel("Cache reads over \(days.count) days: \(Format.tokens(cache)), counted separately from input and output")
            }
            AboutNumbers(text: about(notCollected: notCollected))
            SectionLabel("Daily detail")
            DailyTable(provider: provider, days: days.reversed(), today: today)
        }
    }

    private var cacheTotal: Double? {
        let values = days.compactMap(\.cacheRead)
        return values.isEmpty ? nil : values.reduce(0, +)
    }

    private func about(notCollected: Int) -> String {
        var text = provider.usage.hasSplit
            ? "Input and output tokens per calendar day to \(Format.longDay(today)). Cache tokens are counted separately."
            : "\(provider.name) reports one daily total (no input/output split). Daily totals the provider revises use the latest value."
        text += " The total includes today so far; the average and busiest day use complete measured days only. Missing days are not zero."
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

// MARK: - Empty states and setup notices

/// Replaces cards, chart and table when there is nothing to plot. The message
/// and action come from the collector's detection and configuration, never from
/// the empty dataset alone.
private struct HistoryEmptyState: View {
    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    let provider: Provider
    let availability: Presentation.HistoryAvailability
    let range: Int
    let showAll: () -> Void

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: T.Space.s3) {
                Label(title, systemImage: symbol).font(.auLead).labelStyle(.titleAndIcon)
                Text(message).font(.auBody).foregroundStyle(T.Color.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: T.Space.s2) { actions }
                    .disabled(store.isActing || store.isCollecting)
                ActionBanner()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, T.Space.s2)
        }
        .accessibilityElement(children: .contain)
    }

    private var title: String {
        switch availability {
        case .trackingDisabled: return "Tracking is off for \(provider.name)"
        case .notDetected: return "\(provider.name) not detected"
        case .notConfigured: return "\(provider.name) is not set up"
        case .noUsageRecorded: return "No usage recorded yet"
        case .noneInRange: return "No usage in the last \(range) days"
        case .data: return ""
        }
    }

    private var symbol: String {
        switch availability {
        case .trackingDisabled: return "pause.circle"
        case .notDetected: return "questionmark.circle"
        case .notConfigured: return "wrench.and.screwdriver"
        case .noUsageRecorded, .noneInRange, .data: return "calendar"
        }
    }

    private var message: String {
        switch availability {
        case .trackingDisabled:
            return "The collector doesn’t check \(provider.name), so nothing is recorded. Turning tracking on lets it look for \(provider.name) at its next check; it doesn’t install anything."
        case .notDetected:
            return "Tracking is on, but the collector didn’t find \(provider.name) on this Mac at its last check. Usage appears here once \(provider.name) is installed and used."
        case .notConfigured:
            return Presentation.setupText(provider) ?? "\(provider.name) needs setting up before it can be read."
        case .noUsageRecorded:
            return provider.setup == .waiting
                ? (Presentation.setupText(provider) ?? "Waiting for the first reading.")
                : "Tracking is on and \(provider.name) was found, but no usage has been recorded yet."
        case .noneInRange:
            return "\(provider.name) has earlier history, but nothing was recorded in this range."
        case .data:
            return ""
        }
    }

    @ViewBuilder
    private var actions: some View {
        switch availability {
        case .trackingDisabled:
            Button("Enable tracking") {
                Task { await store.applySettings([.enabling(provider, true)]) }
            }
            .buttonStyle(.borderedProminent).controlSize(.small)
        case .notDetected, .notConfigured:
            Button("Check again") { Task { _ = await store.collectNow() } }.controlSize(.small)
            Button("Provider settings…") { ProviderSettingsRoute.open(provider.id, navigation: navigation) }
                .controlSize(.small)
        case .noUsageRecorded:
            Button("Check again") { Task { _ = await store.collectNow() } }.controlSize(.small)
        case .noneInRange:
            if range < 90 { Button("Show 90 days", action: showAll).controlSize(.small) }
        case .data:
            EmptyView()
        }
    }
}

/// Shown below the chart when history exists but the provider is no longer
/// tracked or found: the history stays visible, with the relevant action.
private struct SetupNotice: View {
    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    let provider: Provider

    var body: some View {
        if !provider.enabled || provider.setup == .disabled {
            notice("Tracking is off for \(provider.name); earlier history is shown.") {
                Button("Enable tracking") {
                    Task { await store.applySettings([.enabling(provider, true)]) }
                }
            }
        } else if provider.setup == .notDetected || provider.setup == .notConfigured {
            notice("\(Presentation.setupText(provider) ?? "\(provider.name) needs attention.") Earlier history is shown.") {
                Button("Provider settings…") { ProviderSettingsRoute.open(provider.id, navigation: navigation) }
            }
        }
    }

    private func notice<Action: View>(_ text: String, @ViewBuilder action: () -> Action) -> some View {
        HStack(spacing: T.Space.s2) {
            Image(systemName: "info.circle").foregroundStyle(T.Color.muted.color).accessibilityHidden(true)
            Text(text).font(.auCaption).foregroundStyle(T.Color.muted.color)
            Spacer(minLength: T.Space.s2)
            action().controlSize(.small).disabled(store.isActing)
        }
        .accessibilityElement(children: .contain)
    }
}

// MARK: - Quota

struct QuotaHistory: View {
    let provider: Provider
    let window: QuotaWindow
    let range: String
    let now: Date
    let compact: Bool
    let gap: TimeInterval

    var body: some View {
        let seconds = RangeText.seconds(range)
        let readings = window.readings.filter { $0.t >= now.addingTimeInterval(-seconds) }
        let resets = window.resets.filter { $0 >= now.addingTimeInterval(-seconds) && $0 <= now }
        VStack(alignment: .leading, spacing: T.Space.s3) {
            SummaryCards(compact: compact, cards: [
                (Presentation.quotaValue(window), "\(Presentation.quotaBasis(window).capitalizedFirst) · \(window.status == .stale ? "stale" : "current")"),
                ("\(readings.count)", "Readings in range"),
                ("\(resets.count)", "Resets in range"),
                (Format.moment(window.measuredAt, now: now), "Latest reading"),
            ])
            Panel { QuotaLineChart(window: window, rangeSeconds: seconds, now: now, gapSeconds: gap) }
            AboutNumbers(text: "Percentage \(window.basis == .remaining ? "remaining" : "used") in \(provider.name)’s \(window.label.lowercased()). Readings are snapshots: they are plotted, never added up\(window.derived ? "; remaining is derived from the reported % used" : "").")
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
