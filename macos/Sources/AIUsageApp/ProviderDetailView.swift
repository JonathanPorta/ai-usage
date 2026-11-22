import AIUsageCore
import AIUsageDesign
import SwiftUI

enum RangeText {
    static func seconds(_ range: String) -> TimeInterval {
        let number = Double(range.dropLast()) ?? 7
        return range.hasSuffix("h") ? number * 3600 : number * 86400
    }

    static func label(_ range: String) -> String {
        let number = Int(range.dropLast()) ?? 0
        if range.hasSuffix("h") { return "\(number) hours" }
        return number % 7 == 0 && number > 7 ? "\(number / 7) weeks" : "\(number) days"
    }
}

/// Provider detail: replaces the overview inside the popover. Header with
/// On/Off and History, then quota, usage chart (key collapsed below it), today,
/// cost and accounting, and sources and diagnostics collapsed at the bottom.
struct ProviderDetailView: View {
    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    let provider: Provider
    let back: () -> Void
    let openHistory: (HistoryContext) -> Void
    let maxHeight: CGFloat

    typealias Metric = DetailMetric
    @State private var metric: Metric = .tokens
    @State private var tokenRange = 7
    @State private var windowId: String?
    @State private var quotaRange: String?
    @State private var chartKeyOpen: Bool
    @State private var sourcesOpen = false
    /// The switch position while its save runs.
    @State private var pendingOn: Bool?
    @State private var saveFailure: String?

    init(provider: Provider, back: @escaping () -> Void, openHistory: @escaping (HistoryContext) -> Void, maxHeight: CGFloat,
         initialMetric: Metric = .tokens, tokenRange: Int = 7, chartKeyOpen: Bool = false) {
        _metric = State(initialValue: initialMetric)
        _tokenRange = State(initialValue: tokenRange)
        _chartKeyOpen = State(initialValue: chartKeyOpen)
        self.provider = provider
        self.back = back
        self.openHistory = openHistory
        self.maxHeight = maxHeight
    }

    private var window: QuotaWindow? {
        let windows = provider.activeWindows
        return windows.first { $0.id == windowId } ?? windows.first
    }

    private var showsQuotaChart: Bool { metric == .quota && !provider.activeWindows.isEmpty }

    var body: some View {
        let now = store.now
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Button(action: back) { Label("Overview", systemImage: Symbols.back) }
                    .buttonStyle(.link)
                    .font(.auBody)
                    .keyboardShortcut(.cancelAction)
                Spacer()
                ToolbarIconButton(symbol: Symbols.history, label: "Open in history window") { openHistory(historyContext) }
            }
            .padding(.horizontal, T.Space.s4).padding(.top, T.Space.s3)
            ScrollView {
                VStack(alignment: .leading, spacing: T.Space.s4) {
                    heading
                    if !provider.activeWindows.isEmpty { quotaSection(now: now) }
                    chartPanel(now: now)
                    todaySection(now: now)
                    if !provider.modelsToday.isEmpty, !DetailPresentation.isNotCollecting(provider) { modelsSection }
                    if !provider.notes.isEmpty { notesSection }
                    costSection(now: now)
                    sourcesSection(now: now)
                }
                .padding(T.Space.s4)
            }
            .frame(maxHeight: maxHeight - 40)
        }
        .onExitCommand(perform: back)
    }

    private var historyContext: HistoryContext {
        DetailPresentation.historyContext(provider, metric: metric, tokenRange: tokenRange,
                                          windowId: windowId, quotaRange: quotaRange)
    }

    // MARK: Header

    private var heading: some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            VStack(alignment: .leading, spacing: 2) {
                Text(provider.name).font(.auTitle).foregroundStyle(T.Color.text.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                Text(provider.product).font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            if let report = store.report, Presentation.showsToggle(provider, in: report) {
                trackingRow(report)
            }
            if let setup = Presentation.setupText(provider), !DetailPresentation.isTrackingOff(provider) {
                Label(setup, systemImage: Symbols.notSetUp).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func trackingRow(_ report: Report) -> some View {
        let toggle = DetailPresentation.trackingToggle(provider, pending: pendingOn)
        return VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: T.Space.s2) {
                Image(systemName: toggle.waiting ? Symbols.waiting : (toggle.isOn ? Symbols.healthy : Symbols.paused))
                    .foregroundStyle(toggle.isOn ? T.Color.success.color : T.Color.muted.color)
                    .accessibilityHidden(true)
                Text(toggle.status).font(.auBody).foregroundStyle(T.Color.text.color)
                if pendingOn != nil { ProgressView().controlSize(.mini) }
                Spacer()
                Toggle("Track \(provider.name)", isOn: Binding(get: { toggle.isOn }, set: { save($0, report: report) }))
                    .toggleStyle(.switch).labelsHidden().controlSize(.small)
                    .disabled(store.isActing)
                    .accessibilityValue(toggle.status)
            }
            if toggle.waiting {
                Text("Auto is still checking whether \(provider.name) is installed. Switching sets On or Off.")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            } else if toggle.autoControlled {
                Text("Auto decides from detection. Switching sets On or Off.")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
            } else if !toggle.isOn {
                Text("Not collecting. Recorded history is kept.")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            if let saveFailure {
                Label("Couldn't save: \(saveFailure)", systemImage: Symbols.failure)
                    .font(.auCaption).foregroundStyle(T.Color.danger.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func save(_ on: Bool, report: Report) {
        guard !store.isActing, pendingOn == nil else { return }
        pendingOn = on
        saveFailure = nil
        let change = DetailPresentation.trackingChange(provider, on: on,
                                                      supportsPolicy: DetailPresentation.supportsPolicy(report))
        Task {
            // Only this save's own outcome counts; nil means it never ran (another
            // action started first), so the switch just returns to the saved state.
            let outcome = await store.applySettings([change])
            if case let .failed(reason)? = outcome { saveFailure = reason }
            // Success, failure or not run: the report's saved state is shown again.
            pendingOn = nil
        }
    }

    private func run(_ action: EmptyCopy.Action) {
        switch action {
        case let .widenRange(days): tokenRange = days
        case let .openHistoryRange(days):
            var context = historyContext
            context.metric = .tokens
            context.tokenRange = days
            openHistory(context)
        case .enableTracking:
            if let report = store.report { save(true, report: report) }
        case .providerSettings: openSettings()
        case .checkAgain: Task { _ = await store.collectNow() }
        }
    }

    private func openSettings() {
        navigation.settingsRequest = SettingsRequest(providerId: provider.id)
    }

    // MARK: Sections

    private func quotaSection(now: Date) -> some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("Quota")
            Panel {
                VStack(alignment: .leading, spacing: T.Space.s3) {
                    ForEach(Array(DetailPresentation.displayWindows(provider).enumerated()), id: \.element.id) { index, window in
                        if index > 0 { Divider() }
                        QuotaCell(window: window, now: now, compact: false)
                    }
                    if let note = DetailPresentation.quotaNote(provider) {
                        Text(note).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    }
                }
            }
        }
    }

    private func chartPanel(now: Date) -> some View {
        let days = Presentation.recentDays(provider, count: tokenRange)
        let empty = DetailPresentation.chartEmpty(provider, days: days, range: tokenRange,
                                                  ranges: DetailPresentation.detailTokenRanges)
        return VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("Usage")
            Panel {
                VStack(alignment: .leading, spacing: T.Space.s3) {
                    HStack(spacing: T.Space.s2) {
                        if !provider.activeWindows.isEmpty {
                            Segmented("Metric", selection: $metric, options: [(.tokens, "Tokens"), (.quota, "Quota")])
                        }
                        Spacer(minLength: 0)
                        if !showsQuotaChart {
                            Segmented("Range", selection: $tokenRange, options: [(7, "7 days"), (30, "30 days")])
                        } else if let window {
                            if provider.activeWindows.count > 1 {
                                Picker("Window", selection: Binding(get: { window.id }, set: { windowId = $0; quotaRange = nil })) {
                                    ForEach(provider.activeWindows) { Text($0.label).tag($0.id) }
                                }
                                .labelsHidden().controlSize(.small).fixedSize()
                            }
                            Segmented("Quota range", selection: Binding(get: { quotaRange ?? window.ranges.first ?? "7d" }, set: { quotaRange = $0 }),
                                      options: window.ranges.map { ($0, RangeText.label($0)) })
                        }
                    }
                    if showsQuotaChart, let window {
                        QuotaLineChart(window: window, rangeSeconds: RangeText.seconds(quotaRange ?? window.ranges.first ?? "7d"),
                                       now: now, gapSeconds: Double(store.report?.thresholds.staleAfterSeconds ?? 7200) * 1.5)
                        ChartKey(isExpanded: $chartKeyOpen) {
                            Text("Percentage \(window.basis == .remaining ? "remaining" : "used") in the \(window.label.lowercased()). Readings are snapshots, plotted and never added up. A dashed rule marks a reset; a dashed tail means the latest reading is stale.")
                        }
                    } else if let empty {
                        CopyEmptyState(empty, action: run)
                    } else {
                        DayBarChart(provider: provider, days: days, style: .full, today: store.report?.today ?? "")
                        ChartKey(isExpanded: $chartKeyOpen) {
                            DayChartLegend(split: provider.usage.hasSplit)
                            Text(provider.usage.hasSplit ? "Input and output per day. Cache tokens are counted separately." : "This provider reports one daily total; input and output aren’t split. Missing days are not zero.")
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func todaySection(now: Date) -> some View {
        let state = DetailPresentation.todayState(provider)
        // With no usage ever, the chart's empty state already says so.
        if state != .noUsageEver || showsQuotaChart {
            VStack(alignment: .leading, spacing: T.Space.s2) {
                SectionLabel("Today")
                switch state {
                case .reading: todayTiles(now: now)
                case .noReadingToday: Panel { InlineEmptyRow(DetailPresentation.noReadingToday) }
                case .notCollecting: Panel { InlineEmptyRow(DetailPresentation.notCollectingToday) }
                case .noUsageEver:
                    Panel { InlineEmptyRow(EmptyCopy(symbol: "chart.bar", title: "No usage recorded yet", detail: "", action: nil)) }
                }
            }
        }
    }

    @ViewBuilder
    private func todayTiles(now: Date) -> some View {
        if let today = provider.today {
            let split = provider.usage.hasSplit
            let notReported = [split ? nil : "input/output split", today.cacheRead == nil ? "cache reads" : nil,
                               today.cacheWrite == nil ? "cache writes" : nil].compactMap { $0 }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: T.Space.s2) {
                if split {
                    StatTile(value: Format.tokens(today.input), caption: "Input")
                    StatTile(value: Format.tokens(today.output), caption: "Output")
                } else {
                    StatTile(value: Format.tokens(today.total), caption: "Total tokens")
                }
                if today.cacheRead != nil { StatTile(value: Format.tokens(today.cacheRead), caption: "Cache reads") }
                if today.cacheWrite != nil { StatTile(value: Format.tokens(today.cacheWrite), caption: "Cache writes") }
            }
            Text((["So far today · measured \(Format.moment(provider.usage.measuredAt, now: now))"]
                  + (notReported.isEmpty ? [] : ["not reported: \(notReported.joined(separator: ", "))"])).joined(separator: " · "))
                .font(.auCaption).foregroundStyle(T.Color.muted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var modelsSection: some View {
        let total = provider.modelsToday.reduce(0) { $0 + $1.tokens }
        return VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("Models today")
            Panel {
                VStack(alignment: .leading, spacing: T.Space.s2) {
                    ForEach(provider.modelsToday, id: \.name) { model in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(model.name).font(.auCaption)
                                Spacer()
                                Text(Format.tokens(model.tokens)).font(.auCaption.monospacedDigit())
                            }
                            Meter(fraction: total > 0 ? model.tokens / total : 0, tint: T.Color.accentSoft.color)
                        }
                        .accessibilityElement(children: .combine)
                    }
                    Text("Input + output so far today · totals \(Format.tokens(total)).")
                        .font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
        }
    }

    private var notesSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(provider.notes, id: \.self) { note in
                Label(note, systemImage: Symbols.info).font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
        }
    }

    private func costSection(now: Date) -> some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("Cost and accounting")
            Panel { AccountingView(provider: provider, now: now, addPrice: openSettings) }
        }
    }

    private func sourcesSection(now: Date) -> some View {
        let problems = provider.sources.filter(\.hasProblem).count
        return AccordionPanel(problems == 0 ? "Sources and diagnostics" : "Sources and diagnostics · \(problems) need\(problems == 1 ? "s" : "") attention",
                              isExpanded: $sourcesOpen) {
            SourcesView(provider: provider, now: now)
        }
    }
}

/// A Panel holding one disclosure group whose whole header row toggles it
/// (the shared full-row disclosure style).
private struct AccordionPanel<Content: View>: View {
    let title: String
    @Binding var isExpanded: Bool
    @ViewBuilder let content: () -> Content

    init(_ title: String, isExpanded: Binding<Bool>, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        _isExpanded = isExpanded
        self.content = content
    }

    var body: some View {
        Panel {
            DisclosureGroup(isExpanded: $isExpanded) {
                content().padding(.top, T.Space.s2)
            } label: {
                Text(title)
            }
            .disclosureGroupStyle(.fullRow)
            .font(.auBody)
        }
    }
}

/// Reported usage cost and the price you entered are always separate rows; a
/// missing one gets its own quiet row, and a price never stands in for cost.
struct AccountingView: View {
    let provider: Provider
    let now: Date
    /// Opens this provider's price field in Settings.
    var addPrice: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: T.Space.s3) {
            if let reported = provider.costs.reported {
                row(Format.usd(reported.amountUsd), reported.basis,
                    "Billing period \(period(reported.periodStart, reported.periodEnd)) · as of \(Format.moment(reported.measuredAt, now: now))")
            } else if let empty = DetailPresentation.reportedCostEmpty(provider) {
                InlineEmptyRow(empty)
            }
            if let api = provider.costs.apiEquivalent {
                row(Format.usd(api.amountUsd), api.basis, "Last \(api.rangeDays) days · not a bill")
            }
            if let subscription = provider.costs.subscription {
                row(Format.usd(subscription.monthlyUsd), "Subscription price", "Per month · entered by you · not usage cost")
            } else if let empty = DetailPresentation.subscriptionEmpty(provider) {
                InlineEmptyRow(empty, action: addPrice)
            }
        }
    }

    private func period(_ start: Date?, _ end: Date?) -> String {
        guard let start, let end else { return "unknown" }
        return "\(Format.moment(start, now: now)) – \(Format.moment(end, now: now))"
    }

    private func row(_ value: String, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.auBody)
                Text(detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            Spacer()
            Text(value).font(.auBody.monospacedDigit())
        }
        .accessibilityElement(children: .combine)
    }
}

struct SourcesView: View {
    let provider: Provider
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: T.Space.s3) {
            ForEach(provider.sources) { source in
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(source.label).font(.auBody.weight(.medium))
                        Spacer()
                        statusLabel(source)
                    }
                    Text("\(source.kind) · read \(Format.moment(source.lastReadAt, now: now)) · tried \(Format.moment(source.lastAttemptAt, now: now))")
                        .font(.auCaption).foregroundStyle(T.Color.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                    if let error = source.error {
                        Text(error).font(.auCaption).foregroundStyle(T.Color.danger.color).textSelection(.enabled)
                    }
                }
                .accessibilityElement(children: .combine)
            }
            Text("Read: last good read. Measured times come from the provider.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color)
        }
    }

    private func statusLabel(_ source: Source) -> some View {
        let (text, symbol, tone): (String, String, ToneLabel.Tone) = {
            switch source.status {
            case .ok: return ("OK", Symbols.healthy, .healthy)
            case .failed: return ("Failed", Symbols.failure, .danger)
            case .auth: return ("Sign-in needed", Symbols.signIn, .danger)
            case .stale: return ("Stale", Symbols.stale, .warning)
            case .off: return ("Off", Symbols.unavailable, .muted)
            case .waiting: return ("Waiting", Symbols.waiting, .muted)
            case .notDetected: return ("Not found", Symbols.notSetUp, .muted)
            }
        }()
        return ToneLabel(text, symbol: symbol, tone: tone)
    }
}
