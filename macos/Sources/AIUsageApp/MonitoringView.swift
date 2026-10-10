import AIUsageCore
import AIUsageDesign
import SwiftUI

/// Monitoring: service (start/stop), schedule (pause/resume), collection
/// (attempts, failures, Collect now), provider sources, and data freshness.
/// Every control reflects what the installed collector can actually do.
struct MonitoringView: View {
    @Environment(AppStore.self) private var store
    let back: () -> Void
    let openDetail: (String) -> Void
    let maxHeight: CGFloat
    @State private var confirmStop = false

    var body: some View {
        let now = store.now
        VStack(alignment: .leading, spacing: 0) {
            Button(action: goBack) { Label("Overview", systemImage: Symbols.back) }
                .buttonStyle(.link)
                .font(.auBody)
                .keyboardShortcut(.cancelAction)
                .padding([.horizontal, .top], T.Space.s4)
            ScrollView {
                VStack(alignment: .leading, spacing: T.Space.s4) {
                    Text("Monitoring").font(.auTitle).accessibilityAddTraits(.isHeader)
                    ActionBanner()
                    if let report = store.report {
                        serviceSection(report)
                        scheduleSection(report, now: now)
                        collectionSection(report, now: now)
                        sourcesSection(report, now: now)
                    }
                    dataSection(now: now)
                }
                .padding(T.Space.s4)
            }
            .frame(maxHeight: maxHeight - 40)
        }
        .onExitCommand(perform: goBack)
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

    private func serviceSection(_ report: Report) -> some View {
        let text = MonitoringText.service(report.service)
        return section("Service") {
            HStack(alignment: .firstTextBaseline) {
                ToneLabel(text.title, symbol: report.service.state == .running ? Symbols.healthy : Symbols.stopped,
                          tone: report.service.state == .running ? .healthy : .danger)
                    .font(.auBody)
                Spacer()
                switch report.service.state {
                case .running, .loaded:
                    if !confirmStop {
                        Button("Stop collector…") { confirmStop = true }.disabled(store.isActing)
                    }
                case .stopped:
                    Button("Start collector") { Task { await store.startService() } }.disabled(store.isActing)
                default:
                    EmptyView()
                }
            }
            Text(text.detail).font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
            if confirmStop && (report.service.state == .running || report.service.state == .loaded) {
                stopConfirmation
            }
        }
    }

    /// Inline confirmation: a sheet or alert would take key focus from the
    /// menu-bar panel, which closes it before the action runs.
    private var stopConfirmation: some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            Text("Stop the collector?").font(.auBody.weight(.semibold)).accessibilityAddTraits(.isHeader)
            Text("It stays stopped, including after you log in, until you start it again here. Your data and settings are kept, and Collect now still runs one-time checks.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
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
                } else {
                    Button { Task { await store.setPaused(true) } } label: { Label("Pause", systemImage: "pause.fill") }
                        .disabled(store.isActing || unavailable != nil || report.service.state != .running)
                }
            }
            Text(text.detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
            if report.schedule.isPaused {
                Text("Resuming schedules the next check one interval later. Use Collect now for an immediate check.")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            if let unavailable {
                Label(unavailable, systemImage: Symbols.info).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func collectionSection(_ report: Report, now: Date) -> some View {
        section("Collection") {
            HStack(alignment: .firstTextBaseline) {
                Text(MonitoringText.lastAttempt(report.collection, now: now)).font(.auBody)
                Spacer()
                Button { Task { await store.collectNow() } } label: {
                    Label(store.isCollecting ? "Checking…" : "Collect now", systemImage: Symbols.collect)
                }
                .disabled(store.isCollecting)
            }
            if store.isCollecting {
                let status = Presentation.status(report, collecting: true, now: now, progress: store.collectProgress)
                Text(status.detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
                if report.collector.capabilities?.progress != true {
                    Text("Per-provider progress needs collector 2.2.0 or newer.").font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
            if let success = report.collection.lastSuccessAt {
                Text("Last successful check \(Format.moment(success, now: now))").font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            ForEach(Array(report.collection.failures.enumerated()), id: \.offset) { _, failure in
                let name = failure.provider.flatMap { report.provider($0)?.name } ?? "Collector"
                Button {
                    if let id = failure.provider { openDetail(id) }
                } label: {
                    Label("\(name) \(failure.kind): \(failure.message ?? "failed")",
                          systemImage: failure.auth ? Symbols.signIn : Symbols.failure)
                        .foregroundStyle(T.Color.danger.color)
                        .multilineTextAlignment(.leading)
                }
                .buttonStyle(.plain)
                .font(.auCaption)
            }
            if let summary = report.collection.summary, !summary.skipped.isEmpty {
                Text("\(summary.skipped.joined(separator: ", ")) off · cached values kept")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
        }
    }

    private func sourcesSection(_ report: Report, now: Date) -> some View {
        section("Provider sources") {
            ForEach(report.providers) { provider in
                VStack(alignment: .leading, spacing: 3) {
                    Button { openDetail(provider.id) } label: {
                        HStack {
                            Text(provider.name).font(.auBody.weight(.medium))
                            Spacer()
                            Image(systemName: Symbols.forward).font(.auCaption).foregroundStyle(T.Color.muted.color)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    ForEach(provider.sources) { source in
                        HStack {
                            Text(source.label).font(.auCaption)
                            Spacer()
                            Text(sourceText(source, now: now)).font(.auCaption)
                                .foregroundStyle(source.hasProblem ? T.Color.warning.color : T.Color.muted.color)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
                if provider.id != report.providers.last?.id { Divider() }
            }
        }
    }

    private func sourceText(_ source: Source, now: Date) -> String {
        switch source.status {
        case .ok: return "OK · read \(Format.moment(source.lastReadAt, now: now))"
        case .failed: return "Failed · last good \(Format.moment(source.lastReadAt, now: now))"
        case .auth: return "Sign-in needed"
        case .stale: return "Stale · read \(Format.moment(source.lastReadAt, now: now))"
        case .off: return "Off"
        case .waiting: return "Waiting"
        case .notDetected: return "Not found"
        }
    }

    private func dataSection(now: Date) -> some View {
        let status = DataStatus.text(generatedAt: store.snapshot?.generatedAt, refreshing: store.isRefreshing,
                                     error: store.refreshError, cached: store.isCachedReport, now: now)
        return section("Data") {
            HStack {
                if store.isRefreshing { ProgressView().controlSize(.mini) }
                Text(status.text).font(.auCaption)
                    .foregroundStyle(status.warning ? T.Color.warning.color : T.Color.muted.color)
                Spacer()
                Button("Re-read") { Task { await store.refresh() } }.disabled(store.isRefreshing)
            }
            Text("Re-reading never contacts providers; Collect now does.").font(.auCaption).foregroundStyle(T.Color.muted.color)
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
        }
    }
}
