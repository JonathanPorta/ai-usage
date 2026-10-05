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

/// Provider detail: replaces the overview inside the popover. Chart first
/// (Tokens 7 days by default), then quota windows, today, models, and the
/// collapsible accounting and diagnostics sections.
struct ProviderDetailView: View {
    @Environment(AppStore.self) private var store
    let provider: Provider
    let back: () -> Void
    let openHistory: () -> Void
    let maxHeight: CGFloat

    enum Metric: Hashable { case tokens, quota }
    @State private var metric: Metric = .tokens
    @State private var tokenRange = 7
    @State private var windowId: String?
    @State private var quotaRange: String?
    @State private var accountingOpen = false
    @State private var sourcesOpen: Bool

    init(provider: Provider, back: @escaping () -> Void, openHistory: @escaping () -> Void, maxHeight: CGFloat,
         initialMetric: Metric = .tokens) {
        _metric = State(initialValue: initialMetric)
        self.provider = provider
        self.back = back
        self.openHistory = openHistory
        self.maxHeight = maxHeight
        _sourcesOpen = State(initialValue: provider.sources.contains { $0.hasProblem })
    }

    private var window: QuotaWindow? {
        let windows = provider.activeWindows
        return windows.first { $0.id == windowId } ?? windows.first
    }

    var body: some View {
        let now = store.now
        VStack(alignment: .leading, spacing: 0) {
            Button(action: back) { Label("Overview", systemImage: Symbols.back) }
                .buttonStyle(.link)
                .font(.auBody)
                .keyboardShortcut(.cancelAction)
                .padding([.horizontal, .top], T.Space.s4)
            ScrollView {
                VStack(alignment: .leading, spacing: T.Space.s4) {
                    heading(now: now)
                    chartPanel(now: now)
                    if !provider.activeWindows.isEmpty { quotaSection(now: now) }
                    todaySection(now: now)
                    if !provider.modelsToday.isEmpty { modelsSection }
                    if !provider.notes.isEmpty { notesSection }
                    moreSection(now: now)
                    Button(action: openHistory) { Label("Open in history window", systemImage: Symbols.history) }
                        .controlSize(.small)
                }
                .padding(T.Space.s4)
            }
            .frame(maxHeight: maxHeight - 40)
        }
        .onExitCommand(perform: back)
    }

    private func heading(now: Date) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(provider.name).font(.auTitle).foregroundStyle(T.Color.text.color)
                .accessibilityAddTraits(.isHeader)
            Text(provider.product).font(.auCaption).foregroundStyle(T.Color.muted.color)
            if let setup = Presentation.setupText(provider) {
                Label(setup, systemImage: Symbols.notSetUp).font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
        }
    }

    private func chartPanel(now: Date) -> some View {
        Panel {
            VStack(alignment: .leading, spacing: T.Space.s3) {
                HStack(spacing: T.Space.s2) {
                    if !provider.activeWindows.isEmpty {
                        Segmented("Metric", selection: $metric, options: [(.tokens, "Tokens"), (.quota, "Quota")])
                    }
                    if metric == .tokens || provider.activeWindows.isEmpty {
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
                if metric == .tokens || provider.activeWindows.isEmpty {
                    let days = Presentation.recentDays(provider, count: tokenRange)
                    DayBarChart(provider: provider, days: days, style: .full)
                    DayChartLegend(split: provider.usage.hasSplit)
                    Text(provider.usage.hasSplit ? "Cache tokens are counted separately." : "This provider reports one daily total; input and output aren’t split.")
                        .font(.auCaption).foregroundStyle(T.Color.muted.color)
                } else if let window {
                    QuotaLineChart(window: window, rangeSeconds: RangeText.seconds(quotaRange ?? window.ranges.first ?? "7d"),
                                   now: now, gapSeconds: Double(store.report?.thresholds.staleAfterSeconds ?? 7200) * 1.5)
                }
            }
        }
    }

    private func quotaSection(now: Date) -> some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("Quota")
            Panel {
                VStack(alignment: .leading, spacing: T.Space.s3) {
                    ForEach(Array(provider.activeWindows.enumerated()), id: \.element.id) { index, window in
                        if index > 0 { Divider() }
                        QuotaCell(window: window, now: now, compact: false)
                    }
                    if let note = provider.quota.note {
                        Text(note).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    }
                }
            }
        }
    }

    private func todaySection(now: Date) -> some View {
        let today = provider.today
        let split = provider.usage.hasSplit
        return VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("Today")
            if let today {
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: T.Space.s2) {
                    if split {
                        StatTile(value: Format.tokens(today.input), caption: "Input")
                        StatTile(value: Format.tokens(today.output), caption: "Output")
                    } else {
                        StatTile(value: Format.tokens(today.total), caption: "Total tokens")
                        StatTile(value: "—", caption: "Input / output · not reported")
                    }
                    StatTile(value: Format.tokens(today.cacheRead), caption: today.cacheRead == nil ? "Cache reads · not reported" : "Cache reads")
                    StatTile(value: Format.tokens(today.cacheWrite), caption: today.cacheWrite == nil ? "Cache writes · not reported" : "Cache writes")
                }
                Text("So far today · measured \(Format.moment(provider.usage.measuredAt, now: now)) · collected \(Format.moment(provider.usage.collectedAt, now: now))")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
            } else {
                Text(provider.usage.status == .none ? "No usage has been recorded for \(provider.name) yet." : "\(provider.name) hasn’t reported today’s usage yet.")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
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

    private func moreSection(now: Date) -> some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("More")
            Panel {
                DisclosureGroup("Cost and accounting", isExpanded: $accountingOpen) {
                    AccountingView(provider: provider, now: now).padding(.top, T.Space.s2)
                }
                .font(.auBody)
            }
            Panel {
                DisclosureGroup("Sources and diagnostics", isExpanded: $sourcesOpen) {
                    SourcesView(provider: provider, now: now).padding(.top, T.Space.s2)
                }
                .font(.auBody)
            }
        }
    }
}

/// Reported usage cost and the price you entered are always separate rows.
struct AccountingView: View {
    let provider: Provider
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            if let reported = provider.costs.reported {
                row(Format.usd(reported.amountUsd), reported.basis,
                    "Billing period \(period(reported.periodStart, reported.periodEnd)) · as of \(Format.moment(reported.measuredAt, now: now))")
            } else {
                row("—", "Reported usage cost", provider.costs.reportedNote ?? "Not reported")
            }
            if let api = provider.costs.apiEquivalent {
                row(Format.usd(api.amountUsd), api.basis, "Last \(api.rangeDays) days · not a bill")
            }
            if let subscription = provider.costs.subscription {
                row(Format.usd(subscription.monthlyUsd), "Subscription price",
                    "per month · entered by you · never compared with quota or cost")
            } else {
                row("—", "Subscription price", "Not entered (monthly_subscription_usd in the collector config)")
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
                    Text(source.kind).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    Text("Last good read \(Format.moment(source.lastReadAt, now: now)) · last attempt \(Format.moment(source.lastAttemptAt, now: now))")
                        .font(.auCaption).foregroundStyle(T.Color.muted.color)
                    if let error = source.error {
                        Text(error).font(.auCaption).foregroundStyle(T.Color.danger.color).textSelection(.enabled)
                    }
                }
                .accessibilityElement(children: .combine)
            }
            Text("Measured times come from the provider; collected times are when the collector read them.")
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
