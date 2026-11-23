import AIUsageCore
import AIUsageDesign
import SwiftUI

/// Monitoring, in order: Collection (last result, Collect now), Schedule (pause/resume),
/// Provider sources (grouped, inactive collapsed), Service (start/stop), Saved data (reread).
/// Every control reflects what the installed collector can actually do.
struct MonitoringView: View {
    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    let back: () -> Void
    let openDetail: (String) -> Void
    let maxHeight: CGFloat
    @State private var confirmStop = false
    @State private var showCollectionDetails: Bool
    @State private var showInactive: Bool
    @State private var showSourceTimes = false
    @State private var showServiceDetails = false

    init(back: @escaping () -> Void, openDetail: @escaping (String) -> Void, maxHeight: CGFloat,
         collectionDetails: Bool = false, inactiveExpanded: Bool = false) {
        self.back = back
        self.openDetail = openDetail
        self.maxHeight = maxHeight
        _showCollectionDetails = State(initialValue: collectionDetails)
        _showInactive = State(initialValue: inactiveExpanded)
    }

    var body: some View {
        let now = store.now
        VStack(alignment: .leading, spacing: 0) {
            Button(action: goBack) { Label("Overview", systemImage: Symbols.back) }
                .buttonStyle(.link)
                .font(.auBody)
                .keyboardShortcut(.cancelAction)
                .accessibilityLabel("Back to overview")
                .padding([.horizontal, .top], T.Space.s4)
            ScrollView {
                VStack(alignment: .leading, spacing: T.Space.s4) {
                    Text("Monitoring").font(.auTitle).accessibilityAddTraits(.isHeader)
                    ActionBanner()
                    if let report = store.report {
                        collectionSection(report, now: now)
                        scheduleSection(report, now: now)
                        sourcesSection(report)
                        serviceSection(report)
                    }
                    dataSection(now: now)
                }
                .padding(T.Space.s4)
            }
            .frame(maxHeight: maxHeight - 40)
        }
        .onExitCommand(perform: goBack)
        // Opening Monitoring acknowledges the warnings the header indicator shows.
        .onAppear { store.acknowledgeWarnings() }
    }

    /// Esc cancels a pending Stop confirmation before it leaves the screen.
    private func goBack() {
        if confirmStop { confirmStop = false } else { back() }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel(title)
            Panel { VStack(alignment: .leading, spacing: T.Space.s2) { content() } }
        }
    }

    private func caption(_ text: String, tone: ThemedColor = T.Color.muted.color) -> some View {
        Text(text).font(.auCaption).foregroundStyle(tone).fixedSize(horizontal: false, vertical: true)
    }

    private func tone(_ severity: CollectionResultFact.Severity) -> ToneLabel.Tone {
        switch severity {
        case .ok: return .healthy
        case .warning: return .warning
        case .critical: return .danger
        case .neutral: return .muted
        }
    }

    // MARK: Collection

    private func collectionSection(_ report: Report, now: Date) -> some View {
        let result = HeaderFacts.result(report)
        let collection = report.collection
        return section("Collection") {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    ToneLabel(result.text, symbol: result.symbol, tone: tone(result.severity))
                        .font(.auBody.weight(.medium))
                    if !result.detail.isEmpty { caption(result.detail) }
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Last check: \(result.accessibilityText)")
                Spacer()
                Button { Task { await store.collectNow() } } label: {
                    HStack(spacing: 4) {
                        if store.isCollecting { ProgressView().controlSize(.mini) } else { Image(systemName: Symbols.collect) }
                        Text(store.isCollecting ? "Checking…" : "Collect now")
                    }
                }
                .disabled(store.isCollecting || nothingToCollect(report))
                .accessibilityLabel(store.isCollecting ? "Checking providers" : "Force manual collection now")
                .accessibilityHint(nothingToCollect(report) ? "Unavailable: no providers are active. Turn one on in Settings." : "")
                .help(nothingToCollect(report) ? "No active providers to collect. Turn one on in Settings."
                      : "Contacts every active provider once")
            }
            if let at = collection.lastAttemptAt {
                caption("Last check \(Format.moment(at, now: now))")
            }
            if let success = MonitoringPage.lastSuccessWorthShowing(collection) {
                caption("Last successful check \(Format.moment(success, now: now))")
            }
            if store.isCollecting {
                let status = Presentation.status(report, collecting: true, now: now, progress: store.collectProgress)
                caption(status.detail)
                if report.collector.capabilities?.progress != true {
                    caption("Per-provider progress needs collector 2.2.0 or newer.")
                }
            }
            if report.service.state != .running {
                Label("The collector isn’t running, so no scheduled checks happen. See Service below.",
                      systemImage: Symbols.stopped)
                    .font(.auCaption).foregroundStyle(T.Color.danger.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(collection.failures.enumerated()), id: \.offset) { _, failure in
                let name = failure.provider.flatMap { report.provider($0)?.name } ?? failure.provider ?? "Collector"
                Button {
                    if let id = failure.provider, report.provider(id) != nil { openDetail(id) }
                } label: {
                    Label("\(name) \(failure.kind): \(failure.message ?? "failed")",
                          systemImage: failure.auth ? Symbols.signIn : Symbols.failure)
                        .foregroundStyle(T.Color.danger.color)
                        .multilineTextAlignment(.leading)
                }
                .buttonStyle(.plain)
                .font(.auCaption)
                .accessibilityHint("Opens \(name)")
            }
            DisclosureGroup(isExpanded: $showCollectionDetails) {
                VStack(alignment: .leading, spacing: 3) {
                    if let at = collection.lastAttemptAt { caption("Attempted \(Format.exactMoment(at))") }
                    if let success = collection.lastSuccessAt { caption("Last success \(Format.exactMoment(success))") }
                    caption(MonitoringPage.exclusions(report) ?? "Every provider that is on was included.")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 2)
            } label: {
                Text("Details").font(.auCaption).accessibilityLabel("Collection details")
            }
            .disclosureGroupStyle(.fullRow)
        }
    }

    private func nothingToCollect(_ report: Report) -> Bool {
        !report.providers.contains(where: MonitoringPage.isActive)
    }

    // MARK: Schedule

    private func scheduleSection(_ report: Report, now: Date) -> some View {
        let text = MonitoringText.schedule(report, now: now)
        let unavailable = MonitoringText.pauseUnavailable(report)
        return section("Schedule") {
            HStack(alignment: .firstTextBaseline) {
                Text(text.title).font(.auBody.weight(.medium))
                Spacer()
                if report.schedule.isPaused {
                    Button { Task { await store.setPaused(false) } } label: { Label("Resume", systemImage: "play.fill") }
                        .disabled(store.isActing)
                        .accessibilityLabel("Resume scheduled checks")
                } else {
                    Button { Task { await store.setPaused(true) } } label: { Label("Pause", systemImage: "pause.fill") }
                        .disabled(store.isActing || unavailable != nil || report.service.state != .running)
                        .accessibilityLabel("Pause scheduled checks")
                }
            }
            caption(text.detail)
            if report.schedule.isPaused {
                caption("Resuming schedules the next check one interval later. Use Collect now for an immediate check.")
            }
            if let unavailable {
                Label(unavailable, systemImage: Symbols.info).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: Provider sources

    private func sourcesSection(_ report: Report) -> some View {
        let groups = MonitoringPage.sourceGroups(report)
        return section("Provider sources") {
            if groups.active.isEmpty {
                HStack(alignment: .firstTextBaseline) {
                    caption("No active providers. Turn one on to start collecting.")
                    Spacer()
                    Button("Open Settings") {
                        navigation.settingsRequest = SettingsRequest(providerId: report.providers.first?.id ?? "")
                    }
                    .accessibilityLabel("Open Settings to turn on a provider")
                }
            }
            ForEach(Array(groups.active.enumerated()), id: \.element.id) { index, group in
                if index > 0 { Divider() }
                sourceGroup(group)
            }
            if !groups.inactive.isEmpty {
                Divider()
                DisclosureGroup(isExpanded: $showInactive) {
                    VStack(alignment: .leading, spacing: T.Space.s2) {
                        ForEach(groups.inactive) { group in sourceGroup(group, inactive: true) }
                    }
                    .padding(.top, T.Space.s1)
                } label: {
                    Text("Inactive (\(groups.inactive.count))").font(.auCaption.weight(.medium))
                        .foregroundStyle(T.Color.muted.color)
                        .accessibilityLabel("Inactive providers, \(groups.inactive.count)")
                }
                .disclosureGroupStyle(.fullRow)
            }
            Toggle("Show exact times", isOn: $showSourceTimes)
                .toggleStyle(.checkbox)
                .font(.auCaption)
                .foregroundStyle(T.Color.muted.color)
                .accessibilityLabel("Show exact read and measurement times")
        }
    }

    private func sourceGroup(_ group: MonitoringPage.SourceGroup, inactive: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Button { openDetail(group.id) } label: {
                HStack {
                    Text(group.name).font(.auBody.weight(.medium))
                        .foregroundStyle(inactive ? T.Color.muted.color : T.Color.text.color)
                    Spacer()
                    Image(systemName: Symbols.forward).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(group.name)\(group.problemCount > 0 ? ", \(group.problemCount) source issue\(group.problemCount == 1 ? "" : "s")" : "")")
            .accessibilityHint("Opens provider details")
            ForEach(group.rows) { row in sourceRow(row) }
        }
    }

    private func sourceRow(_ row: MonitoringPage.SourceRow) -> some View {
        let badge = row.badge
        let color: ThemedColor = switch badge.kind {
        case .healthy: T.Color.success.color
        case .stale, .signIn: T.Color.warning.color
        case .failed: T.Color.danger.color
        case .missing, .off, .waiting: T.Color.muted.color
        }
        return VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: T.Space.s1) {
                Text(row.label).font(.auCaption).foregroundStyle(T.Color.text.color)
                Spacer(minLength: T.Space.s2)
                if !badge.label.isEmpty {
                    Text(badge.label).font(.auCaption).foregroundStyle(color)
                }
                Image(systemName: badge.symbol).font(.auCaption).foregroundStyle(color)
                    .frame(width: 14)
            }
            if showSourceTimes {
                Text(row.detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.leading, T.Space.s2)
        .contentShape(Rectangle())
        .help(row.detail)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(row.label): \(badge.accessibilityLabel)")
        .accessibilityValue(row.detail)
    }

    // MARK: Service

    private func serviceSection(_ report: Report) -> some View {
        let text = MonitoringPage.service(report.service)
        return section("Service") {
            HStack(alignment: .firstTextBaseline) {
                ToneLabel(text.title, symbol: text.running ? Symbols.healthy : Symbols.stopped,
                          tone: text.running ? .healthy : .danger)
                    .font(.auBody.weight(.medium))
                    .accessibilityLabel("Collector service: \(text.title)")
                Spacer()
                switch report.service.state {
                case .running, .loaded:
                    if !confirmStop {
                        Button("Stop collector…") { confirmStop = true }.disabled(store.isActing)
                            .accessibilityLabel("Stop the background collector")
                    }
                case .stopped:
                    Button("Start collector") { Task { await store.startService() } }.disabled(store.isActing)
                        .accessibilityLabel("Start the background collector")
                default:
                    EmptyView()
                }
            }
            if report.service.state == .stopped {
                caption(report.service.disabled == true
                        ? "It stays stopped after you log in until you start it. Data and settings are kept."
                        : "Data and settings are kept.")
            }
            if confirmStop && (report.service.state == .running || report.service.state == .loaded) {
                stopConfirmation
            }
            if !text.technical.isEmpty {
                DisclosureGroup(isExpanded: $showServiceDetails) {
                    caption(text.technical).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } label: {
                    Text("Technical details").font(.auCaption).accessibilityLabel("Technical service details")
                }
                .disclosureGroupStyle(.fullRow)
            }
        }
    }

    /// Inline confirmation: a sheet or alert would take key focus from the
    /// menu-bar panel, which closes it before the action runs.
    private var stopConfirmation: some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            Text("Stop the collector?").font(.auBody.weight(.semibold)).accessibilityAddTraits(.isHeader)
            caption("It stays stopped, including after you log in, until you start it again here. Your data and settings are kept, and Collect now still runs one-time checks.")
            HStack {
                Spacer()
                Button("Keep running") { confirmStop = false }
                Button("Stop collector", role: .destructive) {
                    confirmStop = false
                    Task { await store.stopService() }
                }
                .foregroundStyle(T.Color.danger.color)
            }
        }
        .padding(.top, T.Space.s2)
    }

    // MARK: Saved data

    private func dataSection(now: Date) -> some View {
        let status = DataStatus.text(generatedAt: store.snapshot?.generatedAt, refreshing: store.isRefreshing,
                                     error: store.refreshError, cached: store.isCachedReport, now: now)
        return section("Saved data") {
            HStack {
                if store.isRefreshing { ProgressView().controlSize(.mini) }
                Text(status.text).font(.auCaption)
                    .foregroundStyle(status.warning ? T.Color.warning.color : T.Color.muted.color)
                Spacer()
                Button("Reread saved data") { Task { await store.refresh() } }
                    .disabled(store.isRefreshing)
                    .accessibilityLabel("Reread saved data from disk")
            }
            caption("Rereading never contacts providers; Collect now does.")
        }
    }
}

/// Outcome of the last service or settings action.
struct ActionBanner: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        switch store.action {
        case .idle:
            EmptyView()
        case let .running(text):
            HStack(spacing: T.Space.s2) { ProgressView().controlSize(.mini); Text(text).font(.auCaption) }
        case let .done(text):
            NoticeBanner(notice: .init(id: "action", tone: .info, title: text, detail: "", providerId: nil),
                         more: 0, action: store.dismissAction, actionTitle: "Dismiss")
        case let .failed(text):
            NoticeBanner(notice: .init(id: "action", tone: .failure, title: "That didn’t work", detail: text, providerId: nil),
                         more: 0, action: store.dismissAction, actionTitle: "Dismiss")
                // Seen: leaving this screen may clear it (AppStore.screenChanged).
                .task(id: store.action) { store.actionBannerShown() }
        }
    }
}
