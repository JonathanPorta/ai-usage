import AIUsageCore
import AIUsageDesign
import SwiftUI

struct OverviewView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let openDetail: (String) -> Void
    let openHistory: (String?) -> Void
    let openMonitoring: () -> Void
    let openSettings: () -> Void
    @Binding var returnFocus: String?
    /// Owned by PopoverRoot: focus rings show only while the keyboard is in use.
    @Binding var focusRingsVisible: Bool
    let maxHeight: CGFloat

    @State private var pinnedHeight: CGFloat = 120
    @State private var footerHeight: CGFloat = 44
    @State private var listHeight: CGFloat = 400
    @State private var showingDetails = false

    @FocusState private var focus: FocusTarget?

    enum FocusTarget: Hashable { case monitoring, result, card(String) }

    var body: some View {
        let now = store.now
        VStack(spacing: 0) {
            VStack(spacing: T.Space.s2) {
                header(now: now)
                banner
            }
            .padding([.horizontal, .top], T.Space.s4)
            .padding(.bottom, T.Space.s3)
            .readHeight($pinnedHeight)

            ScrollViewReader { proxy in
                ScrollView {
                    list(now: now)
                        .padding(.horizontal, T.Space.s4)
                        .padding(.bottom, T.Space.s3)
                        .readHeight($listHeight)
                }
                // Keyboard focus moving to a card scrolls it fully into view.
                .onChange(of: focus) { _, target in
                    guard case let .card(id) = target else { return }
                    withAnimation(reduceMotion ? nil : .easeOut(duration: 0.15)) { proxy.scrollTo(id) }
                }
            }
            .frame(height: max(80, min(listHeight, maxHeight - pinnedHeight - footerHeight)))

            Divider()
            toolbar.readHeight($footerHeight)
        }
        .onKeyPress(.downArrow) { moveFocus(1) }
        .onKeyPress(.upArrow) { moveFocus(-1) }
        .onChange(of: returnFocus) { _, id in
            if let id { focus = .card(id); returnFocus = nil }
        }
        // Routine completion feedback is brief; a failed run stays until dismissed.
        .task(id: store.collect) {
            guard case let .finished(run) = store.collect, run.outcome != .failed else { return }
            try? await Task.sleep(for: .seconds(8))
            if case .finished(run) = store.collect { store.dismissCollectResult() }
        }
    }

    // MARK: Header

    private func header(now: Date) -> some View {
        let monitoring = HeaderFacts.monitoring(store.report, collecting: store.isCollecting, now: now)
        return HStack(alignment: .center, spacing: T.Space.s2) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: T.Space.s1) {
                    Text("My AI Usage").font(.auTitle).foregroundStyle(T.Color.text.color)
                        .accessibilityAddTraits(.isHeader)
                    if AppDefaults.isSandbox {
                        Text("Sandbox").font(.auCaption).foregroundStyle(T.Color.muted.color)
                            .padding(.horizontal, 4).overlay(Capsule().stroke(T.Color.muted.color, lineWidth: 1))
                    }
                }
                subtitle(monitoring)
            }
            Spacer(minLength: T.Space.s2)
            resultIndicator
            monitoringButton(monitoring)
        }
    }

    /// One quiet line that always fits: the monitoring state and when it last checked, or
    /// the immediate outcome of a manual check. The full sentence is in the ECG tooltip.
    @ViewBuilder
    private func subtitle(_ monitoring: MonitoringFact) -> some View {
        let checked = store.report?.collection.lastAttemptAt.map { "checked \(Format.ago($0, now: store.now))" }
        let text: String = {
            if store.isCollecting {
                if let progress = store.collectProgress, let name = store.report?.provider(progress.providerId)?.name {
                    return "Checking \(name) · \(progress.index) of \(progress.total)"
                }
                return "Checking providers…"
            }
            if case let .finished(run) = store.collect, run.outcome != .failed {
                let count = issues.count
                if count == 0 && run.outcome == .complete && HeaderFacts.result(store.report).kind == .success {
                    return "Check complete"
                }
                return count > 0 ? "Check finished · \(count) issue\(count == 1 ? "" : "s")" : "Check finished"
            }
            let state: String
            switch monitoring.state {
            case .monitoring: state = "Monitoring"
            case .paused: state = "Paused"
            case .stopped: state = "Collector stopped"
            case .checking: state = "Checking"
            default: return monitoring.text
            }
            return [state, checked].compactMap { $0 }.joined(separator: " · ")
        }()
        let danger = monitoring.state == .stopped || monitoring.state == .notInstalled
        Text(text)
            .font(.auCaption)
            .foregroundStyle(danger ? T.Color.danger.color : T.Color.muted.color)
            .lineLimit(1)
            .help(monitoring.text)
    }

    private func monitoringTint(_ state: MonitoringFact.State) -> ThemedColor {
        switch state {
        case .monitoring: return T.Color.success.color
        case .checking, .paused: return T.Color.accent.color
        case .stopped, .notInstalled: return T.Color.danger.color
        case .waitingFirstRun, .unavailable: return T.Color.muted.color
        }
    }

    private func monitoringButton(_ fact: MonitoringFact) -> some View {
        let tint = monitoringTint(fact.state)
        return Button(action: openMonitoring) {
            Image(systemName: Symbols.appMark)
                .font(.system(size: 17, weight: .medium))
                .foregroundStyle(tint)
                .symbolEffect(.pulse, options: .repeating, isActive: fact.state == .checking && !reduceMotion)
                .frame(width: 36, height: 36)
                .background(T.Color.panel.color, in: RoundedRectangle(cornerRadius: T.Radius.medium))
                .overlay(RoundedRectangle(cornerRadius: T.Radius.medium).strokeBorder(T.Color.line.color))
                .overlay(alignment: .bottomTrailing) {
                    Image(systemName: fact.symbol)
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(tint)
                        .background(Circle().fill(T.Color.panel.color).padding(-1.5))
                        .offset(x: 3, y: 3)
                }
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .focused($focus, equals: .monitoring)
        .help("\(fact.text)\n\(fact.tooltip)")
        .accessibilityLabel("Monitoring")
        .accessibilityValue(fact.accessibilityText)
        .accessibilityHint("Opens Monitoring")
    }

    // MARK: Collection result and issues

    private var issues: OverviewIssues {
        OverviewIssues(report: store.report, now: store.now, detections: store.detectionNotices,
                       refreshError: store.report == nil ? nil : store.refreshError, collect: store.collect,
                       acknowledged: store.warningsAcknowledged)
    }

    private var resultIndicator: some View {
        let issues = issues
        let tint: ThemedColor = {
            switch issues.level {
            case .critical: return T.Color.danger.color
            case .warning: return T.Color.warning.color
            case .ok: return T.Color.success.color
            case .neutral: return T.Color.muted.color
            }
        }()
        return Button { showingDetails.toggle() } label: {
            HStack(spacing: 4) {
                Image(systemName: issues.symbol).font(.system(size: 13, weight: .semibold))
                if issues.count > 0 {
                    Text("\(issues.count)").font(.auCaption.weight(.semibold).monospacedDigit())
                }
            }
            .foregroundStyle(tint)
            .padding(.horizontal, T.Space.s2)
            .frame(minWidth: 36, minHeight: 28)
            .background {
                Capsule(style: .circular).fill(issues.level == .critical ? T.Color.dangerBackground.color
                               : issues.level == .warning ? T.Color.warningBackground.color : T.Color.panel.color)
                Capsule(style: .circular).strokeBorder(T.Color.line.color, lineWidth: 1)
            }
            .fixedSize()
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        // A capsule ring of our own; the system's rectangular ring clipped at the ends.
        .overlay(Capsule().strokeBorder(T.Color.focus.color, lineWidth: 2).padding(-2)
            .opacity(focus == .result && focusRingsVisible ? 1 : 0))
        .focused($focus, equals: .result)
        .help(issues.tooltip)
        .accessibilityLabel("Last collection")
        .accessibilityValue(issues.accessibilityText)
        .accessibilityHint("Shows the latest collection details")
        // Looking at the details acknowledges the current warnings.
        .onChange(of: showingDetails) { _, shown in if shown { store.acknowledgeWarnings() } }
        .popover(isPresented: $showingDetails, arrowEdge: .bottom) {
            CollectionDetailsView(issues: issues, now: store.now,
                                  openDetail: { id in showingDetails = false; openDetail(id) },
                                  openMonitoring: { showingDetails = false; openMonitoring() },
                                  openSettings: { showingDetails = false; openSettings() },
                                  acknowledge: store.acknowledgeDetection)
        }
        .focusEffectDisabled()
    }

    /// The prominent banner is reserved for failures that leave nothing usable on screen,
    /// and for a manual check that could not run at all.
    @ViewBuilder
    private var banner: some View {
        if store.report == nil, case let .failed(message) = store.phase {
            NoticeBanner(notice: .init(id: "report", tone: .failure, title: "Collector data couldn’t be read",
                                       detail: message, providerId: nil), more: 0, action: nil)
        } else if case let .finished(run) = store.collect, run.outcome == .failed {
            NoticeBanner(notice: .init(id: "collect", tone: .failure, title: "Manual check failed",
                                       detail: run.message, providerId: nil),
                         more: 0, action: nil, dismiss: store.dismissCollectResult, dismissLabel: "Dismiss check result")
        }
    }

    // MARK: Cards

    @ViewBuilder
    private func list(now: Date) -> some View {
        if let report = store.report {
            let cards = store.cardProviders
            if cards.isEmpty {
                if store.isAwaitingDetection {
                    QuietEmptyState(symbol: nil, title: "Looking for your AI tools",
                                    detail: "Cards appear here after the first check finds them.", busy: true)
                } else {
                    QuietEmptyState(symbol: "square.grid.2x2", title: "Choose your providers",
                                    detail: "No provider is on or detected on this Mac. Pick the tools you use in Settings.",
                                    actionTitle: "Open Settings", action: openSettings)
                }
            } else {
                LazyVStack(spacing: T.Space.s2) {
                    ForEach(cards) { provider in
                        ProviderCard(provider: provider, now: now, today: report.today, checking: checking(provider),
                                     issue: Presentation.cardIssue(provider, in: report),
                                     open: { openDetail(provider.id) })
                            .themeScope(provider.id)
                            .focused($focus, equals: .card(provider.id))
                            .id(provider.id)
                    }
                    if store.isAwaitingDetection {
                        Label("Still looking for other tools…", systemImage: Symbols.waiting)
                            .font(.auCaption).foregroundStyle(T.Color.muted.color)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        } else if case .loading = store.phase {
            HStack(spacing: T.Space.s2) {
                ProgressView().controlSize(.small)
                Text("Reading collector data…").font(.auBody).foregroundStyle(T.Color.muted.color)
            }
            .frame(maxWidth: .infinity, minHeight: 120)
        }
    }

    private func checking(_ provider: Provider) -> Bool {
        guard store.isCollecting, provider.isActive else { return false }
        // With per-provider progress, tag only the provider being read.
        if let progress = store.collectProgress { return progress.providerId == provider.id }
        return true
    }

    // MARK: Toolbar

    private var toolbar: some View {
        HStack(spacing: T.Space.s4) {
            ToolbarIconButton(symbol: Symbols.collect, label: "Force manual collection now", busy: store.isCollecting) {
                Task { await store.collectNow() }
            }
            .keyboardShortcut("r", modifiers: .command)
            .disabled(store.isCollecting)
            ToolbarIconButton(symbol: Symbols.history, label: "See history") { openHistory(nil) }
            ToolbarIconButton(symbol: Symbols.settings, label: "Show settings", action: openSettings)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, T.Space.s2)
    }

    private func moveFocus(_ delta: Int) -> KeyPress.Result {
        guard store.report != nil else { return .ignored }
        if !focusRingsVisible {
            focusRingsVisible = true
            // The first arrow press shows where focus already is.
            if focus != nil { return .handled }
        }
        // The same filtered, ordered list the cards render.
        let order: [FocusTarget] = [.result, .monitoring] + store.cardProviders.map { .card($0.id) }
        let index = focus.flatMap { order.firstIndex(of: $0) } ?? (delta > 0 ? -1 : order.count)
        let next = max(0, min(order.count - 1, index + delta))
        focus = order[next]
        return .handled
    }
}

// MARK: - Issues

/// Everything the result indicator summarizes. A newer success never hides an
/// unresolved provider failure, detection notice or refresh error.
struct OverviewIssues {
    enum Level: Int, Comparable {
        case neutral, ok, warning, critical
        static func < (a: Level, b: Level) -> Bool { a.rawValue < b.rawValue }
    }

    let result: CollectionResultFact
    let lastAttempt: String?
    /// Collection issues only: source/provider failures from the last check.
    let notices: [Presentation.Notice]
    /// Quota exhaustion: listed separately, never a collection issue.
    let limits: [Presentation.Notice]
    let detections: [DetectionNotice]
    let refreshError: String?
    /// The user has already seen exactly these warnings; a warning shows as neutral.
    let acknowledged: Bool

    /// `collect` is accepted for call-site symmetry; a failed manual run is shown by the
    /// banner alone, so it is never counted here too.
    init(report: Report?, now: Date, detections: [DetectionNotice], refreshError: String?, collect: AppStore.CollectState = .idle,
         acknowledged: Bool = false) {
        result = HeaderFacts.result(report)
        lastAttempt = report.map { MonitoringText.lastAttempt($0.collection, now: now) }
        let all = report.map { Presentation.notices($0, now: now) } ?? []
        notices = all.filter { $0.tone != .limit }
        limits = all.filter { $0.tone == .limit }
        self.detections = detections
        self.refreshError = refreshError
        self.acknowledged = acknowledged
    }

    /// Exactly the number of rows under "Collection issues" in the details popover.
    var count: Int { notices.count + detections.count + (refreshError == nil ? 0 : 1) }

    var level: Level {
        var level: Level = {
            switch result.severity {
            case .ok: return .ok
            case .warning: return .warning
            case .critical: return .critical
            case .neutral: return .neutral
            }
        }()
        if count > 0 { level = max(level, .warning) }
        if notices.contains(where: { $0.id == "collector" }) { level = .critical }
        // Seen warnings go quiet until the set changes; critical never does.
        if level == .warning && acknowledged { level = .neutral }
        return level
    }

    var symbol: String {
        switch level {
        case .critical: return "xmark.octagon.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .ok, .neutral: return result.symbol
        }
    }

    var tooltip: String {
        var lines = [result.detail.isEmpty ? result.text : "\(result.text) · \(result.detail)"]
        if count > 0 { lines.append("\(count) collection issue\(count == 1 ? "" : "s") · click for details") }
        return lines.joined(separator: "\n")
    }

    var accessibilityText: String {
        var text = result.accessibilityText
        if count > 0 { text += ". \(count) collection issue\(count == 1 ? "" : "s")" }
        return text
    }
}

/// The latest collection, opened from the result indicator.
struct CollectionDetailsView: View {
    let issues: OverviewIssues
    let now: Date
    let openDetail: (String) -> Void
    let openMonitoring: () -> Void
    let openSettings: () -> Void
    let acknowledge: (DetectionNotice) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: T.Space.s3) {
            VStack(alignment: .leading, spacing: 2) {
                Text(issues.result.text).font(.auBody.weight(.semibold)).foregroundStyle(T.Color.text.color)
                if !issues.result.detail.isEmpty {
                    Text(issues.result.detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
                if let lastAttempt = issues.lastAttempt {
                    Text(lastAttempt).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
            if issues.count > 0 {
                SectionLabel("Collection issues")
                if let error = issues.refreshError {
                    item(symbol: Symbols.stale, tint: T.Color.warning.color, title: "Showing the last report", detail: error) { EmptyView() }
                }
                ForEach(issues.detections) { notice in
                    item(symbol: Symbols.info, tint: T.Color.warning.color, title: "\(notice.providerName): \(notice.title)",
                         detail: "History is kept either way.") {
                        HStack(spacing: T.Space.s3) {
                            Button(DetectionNotice.Action.keepHistory.label) { acknowledge(notice) }
                                .buttonStyle(.link)
                            Button(DetectionNotice.Action.reviewSettings.label) { acknowledge(notice); openSettings() }
                                .buttonStyle(.link)
                        }
                    }
                }
                ForEach(issues.notices) { notice in
                    item(symbol: notice.title.contains("sign in") ? Symbols.signIn : Symbols.failure,
                         tint: T.Color.danger.color, title: notice.title, detail: notice.detail) {
                        if let id = notice.providerId {
                            Button("Details") { openDetail(id) }.buttonStyle(.link)
                        }
                    }
                }
            }
            if !issues.limits.isEmpty {
                SectionLabel("Quota limits")
                ForEach(issues.limits) { notice in
                    item(symbol: Symbols.limit, tint: T.Color.warning.color, title: notice.title, detail: notice.detail) {
                        if let id = notice.providerId {
                            Button("Details") { openDetail(id) }.buttonStyle(.link)
                        }
                    }
                }
            }
            Divider()
            Button("Open Monitoring", action: openMonitoring).buttonStyle(.link)
        }
        .font(.auCaption)
        .padding(T.Space.s3)
        .frame(width: 320, alignment: .leading)
    }

    private func item<Actions: View>(symbol: String, tint: ThemedColor, title: String, detail: String,
                                     @ViewBuilder actions: () -> Actions) -> some View {
        HStack(alignment: .top, spacing: T.Space.s2) {
            Image(systemName: symbol).foregroundStyle(tint).frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.auCaption.weight(.semibold)).foregroundStyle(T.Color.text.color)
                    .fixedSize(horizontal: false, vertical: true)
                if !detail.isEmpty {
                    Text(detail).foregroundStyle(T.Color.muted.color).lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                actions()
            }
        }
    }
}

// MARK: - Provider card

struct ProviderCard: View {
    let provider: Provider
    let now: Date
    let today: String
    let checking: Bool
    var issue: (text: String, severity: Presentation.CardIssueSeverity)? = nil
    @FocusState private var statsFocused: Bool
    let open: () -> Void

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: T.Space.s3) {
                titleRow
                content
            }
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: open)
        // Without a reveal host, a focused trophy's local overlay draws above later cards.
        .zIndex(statsFocused ? 1 : 0)
    }

    private var titleRow: some View {
        HStack(spacing: T.Space.s2) {
            Button(action: open) {
                HStack(spacing: T.Space.s2) {
                    Text(provider.name).font(.auBody.weight(.semibold)).foregroundStyle(T.Color.text.color)
                        .lineLimit(1).truncationMode(.tail)
                    if checking {
                        Text("Checking").font(.auCaption).foregroundStyle(T.Color.accent.color)
                            .padding(.horizontal, 6).padding(.vertical, 1)
                            .background(T.Color.accentBackground.color, in: Capsule())
                            .fixedSize()
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(provider.name)
            .accessibilityLabel("\(provider.name), open details")
            StatisticsButton(provider: provider, now: now, focused: $statsFocused).fixedSize()
            Image(systemName: Symbols.forward).font(.auCaption).foregroundStyle(T.Color.muted.color)
                .accessibilityHidden(true)
        }
    }

    @ViewBuilder
    private var content: some View {
        if let setup = Presentation.setupText(provider), provider.days.allSatisfy({ !$0.hasValue }) {
            Label(setup, systemImage: provider.setup == .disabled ? Symbols.unavailable : Symbols.notSetUp)
                .font(.auCaption).foregroundStyle(T.Color.muted.color)
        } else {
            let windows = provider.activeWindows
            if !windows.isEmpty {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: T.Space.s4, alignment: .top),
                                         count: windows.count > 1 ? 2 : 1),
                          alignment: .leading, spacing: T.Space.s3) {
                    ForEach(windows) { QuotaCell(window: $0, now: now) }
                }
            }
            usageRow
            captions
        }
    }

    private var usageRow: some View {
        let recent = Presentation.recentDays(provider, count: 7)
        let measuredDays = recent.filter { $0.hasValue }.count
        let figure = CardStatistics.today(provider)
        return HStack(alignment: .top, spacing: T.Space.s3) {
            VStack(alignment: .leading, spacing: 1) {
                Text(figure.value)
                    .font(.system(size: 17, weight: .medium).monospacedDigit())
                    .foregroundStyle(figure.state == .missing || figure.state == .neverCollected
                                     ? T.Color.muted.color : T.Color.text.color)
                Text(figure.note).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(width: 96, alignment: .leading)
            .accessibilityElement(children: .combine)
            if measuredDays >= 2 {
                DayBarChart(provider: provider, days: recent, style: .mini, today: today)
            } else {
                Text("Not enough days measured for a 7-day chart yet · \(Presentation.seriesLabel(provider))")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    @ViewBuilder
    private var captions: some View {
        let usage = Presentation.cardFreshness(provider, now: now)
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: T.Space.s3) {
                if let unavailable = Presentation.quotaUnavailableText(provider) {
                    Label(unavailable, systemImage: Symbols.unavailable).foregroundStyle(T.Color.muted.color)
                }
                if provider.quota.status == .mixed {
                    Label("Quota freshness mixed", systemImage: Symbols.stale).foregroundStyle(T.Color.warning.color)
                }
            }
            Text(usage.text).foregroundStyle(usage.warning ? T.Color.warning.color : T.Color.muted.color)
            if let issue {
                // The card's one issue line, one severity.
                Label(issue.text, systemImage: issue.severity == .signIn ? Symbols.signIn
                      : issue.severity == .failure ? Symbols.failure : Symbols.stale)
                    .foregroundStyle(issue.severity == .stale ? T.Color.warning.color : T.Color.danger.color)
            }
        }
        .font(.auCaption)
        .lineLimit(2)
    }
}

/// The trophy beside a card title. Hover, click or keyboard activation shows the statistics.
struct StatisticsButton: View {
    let provider: Provider
    let now: Date
    var focused: FocusState<Bool>.Binding
    @State private var showing = false
    @State private var overButton = false
    @State private var overPopover = false
    /// Computed when the provider's data changes, not on every timer tick.
    @State private var stats: ProviderStatistics?

    private var statsKey: String {
        "\(provider.id)|\(provider.days.count)|\(provider.today?.total ?? -1)|\(provider.lastActivityAt?.timeIntervalSince1970 ?? 0)|\(provider.days.last?.date ?? "")"
    }

    var body: some View {
        Button { showing = true } label: {
            Image(systemName: "trophy")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(T.Color.muted.color)
                .frame(width: 24, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .focused(focused)
        .onHover { inside in
            overButton = inside
            if inside { showing = true } else { closeSoon() }
        }
        .popover(isPresented: $showing, arrowEdge: .bottom) {
            ProviderStatisticsView(name: provider.name, stats: stats ?? CardStatistics.statistics(provider, now: now))
                .onHover { inside in
                    overPopover = inside
                    if !inside { closeSoon() }
                }
        }
        // Keyboard focus shows the statistics as a panel that never takes focus. The
        // window-root reveal host draws it above this card's rows and later cards.
        .focusRevealPanel(focused.wrappedValue && !showing && stats != nil) {
            if let stats {
                ProviderStatisticsView(name: provider.name, stats: stats)
                    .background(T.Color.panel.color, in: RoundedRectangle(cornerRadius: T.Radius.medium))
                    .overlay(RoundedRectangle(cornerRadius: T.Radius.medium).strokeBorder(T.Color.line.color))
                    .compositingGroup()
                    .shadow(color: .black.opacity(0.15), radius: 6, y: 2)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
        .task(id: statsKey) { stats = CardStatistics.statistics(provider, now: now) }
        .help("Usage statistics")
        .accessibilityLabel("\(provider.name) usage statistics")
        .accessibilityValue(stats?.accessibilitySummary ?? "")
    }

    private func closeSoon() {
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(350))
            if !overButton && !overPopover { showing = false }
        }
    }
}
