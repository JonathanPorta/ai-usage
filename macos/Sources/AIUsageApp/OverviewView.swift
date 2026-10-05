import AIUsageCore
import AIUsageDesign
import SwiftUI

struct OverviewView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let openDetail: (String) -> Void
    let openHistory: (String?) -> Void
    @Binding var returnFocus: String?
    let maxHeight: CGFloat

    @State private var pinnedHeight: CGFloat = 200
    @State private var listHeight: CGFloat = 400
    @FocusState private var focus: FocusTarget?

    enum FocusTarget: Hashable { case status, card(String) }

    var body: some View {
        let now = store.now
        VStack(spacing: 0) {
            VStack(spacing: T.Space.s2) {
                header
                StatusRow(summary: Presentation.status(store.report, collecting: store.isCollecting, now: now),
                          reduceMotion: reduceMotion)
                    .focusable()
                    .focused($focus, equals: .status)
                notice(now: now)
            }
            .padding([.horizontal, .top], T.Space.s4)
            .padding(.bottom, T.Space.s2)
            .readHeight($pinnedHeight)

            ScrollView {
                list(now: now)
                    .padding(.horizontal, T.Space.s4)
                    .padding(.bottom, T.Space.s3)
                    .readHeight($listHeight)
            }
            .frame(height: max(80, min(listHeight, maxHeight - pinnedHeight - 52)))

            Divider()
            footer
        }
        .onKeyPress(.downArrow) { moveFocus(1) }
        .onKeyPress(.upArrow) { moveFocus(-1) }
        .onChange(of: returnFocus) { _, id in
            if let id { focus = .card(id); returnFocus = nil }
        }
    }

    // MARK: Regions

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 2) {
                Text("AI Usage").font(.auTitle).foregroundStyle(T.Color.text.color)
                Text("Your AI tools, at a glance").font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            Spacer()
            Image(systemName: Symbols.appMark)
                .font(.system(size: 17, weight: .medium))
                .foregroundStyle(T.Color.accent.color)
                .frame(width: 36, height: 36)
                .background(T.Color.panel.color, in: RoundedRectangle(cornerRadius: T.Radius.medium))
                .overlay(RoundedRectangle(cornerRadius: T.Radius.medium).strokeBorder(T.Color.line.color))
                .accessibilityHidden(true)
        }
    }

    @ViewBuilder
    private func notice(now: Date) -> some View {
        if case let .finished(run) = store.collect {
            CollectResultBanner(run: run, report: store.report, dismiss: store.dismissCollectResult)
        } else if case let .failed(message) = store.phase {
            NoticeBanner(notice: .init(id: "report", tone: .failure, title: "Collector data couldn’t be read",
                                       detail: message, providerId: nil), more: 0, action: nil)
        } else if let report = store.report {
            let notices = Presentation.notices(report, now: now)
            if let first = notices.first {
                NoticeBanner(notice: first, more: notices.count - 1,
                             action: first.providerId.map { id in { openDetail(id) } })
            } else if let error = store.refreshError {
                NoticeBanner(notice: .init(id: "refresh", tone: .stale, title: "Showing the last report",
                                           detail: error, providerId: nil), more: 0, action: nil)
            }
        }
    }

    @ViewBuilder
    private func list(now: Date) -> some View {
        if let report = store.report {
            LazyVStack(spacing: T.Space.s2) {
                ForEach(report.providers) { provider in
                    ProviderCard(provider: provider, now: now, checking: store.isCollecting && provider.isActive,
                                 open: { openDetail(provider.id) })
                        .focused($focus, equals: .card(provider.id))
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

    private var footer: some View {
        HStack {
            Button {
                Task { await store.collectNow() }
            } label: {
                Label(store.isCollecting ? "Checking…" : "Collect now", systemImage: Symbols.collect)
            }
            .buttonStyle(.borderless)
            .foregroundStyle(T.Color.accent.color)
            .fontWeight(.semibold)
            .keyboardShortcut("r", modifiers: .command)
            .disabled(store.isCollecting)
            .help("Run one collection now (⌘R). It never starts or stops the background collector.")
            Spacer()
            Button { openHistory(nil) } label: { Label("Open history", systemImage: Symbols.history) }
                .buttonStyle(.borderless)
                .foregroundStyle(T.Color.text.color)
        }
        .font(.auBody)
        .padding(.horizontal, T.Space.s4)
        .padding(.vertical, T.Space.s3)
    }

    private func moveFocus(_ delta: Int) -> KeyPress.Result {
        guard let report = store.report else { return .ignored }
        let order: [FocusTarget] = [.status] + report.providers.map { .card($0.id) }
        let index = focus.flatMap { order.firstIndex(of: $0) } ?? (delta > 0 ? -1 : order.count)
        let next = max(0, min(order.count - 1, index + delta))
        focus = order[next]
        return .handled
    }
}

// MARK: - Provider card

struct ProviderCard: View {
    let provider: Provider
    let now: Date
    let checking: Bool
    let open: () -> Void

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: T.Space.s2) {
                Button(action: open) {
                    HStack {
                        Text(provider.name).font(.auBody.weight(.semibold)).foregroundStyle(T.Color.text.color)
                        if checking {
                            Text("Checking").font(.auCaption).foregroundStyle(T.Color.accent.color)
                                .padding(.horizontal, 6).padding(.vertical, 1)
                                .background(T.Color.accentBackground.color, in: Capsule())
                        }
                        Spacer()
                        Image(systemName: Symbols.forward).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(provider.name), open details")
                Divider()
                content
            }
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: open)
        .opacity(provider.setup == .ready ? 1 : 0.8)
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
                Divider()
            }
            usageRow
            captions
        }
    }

    private var usageRow: some View {
        let recent = Presentation.recentDays(provider, count: 7)
        let measuredDays = recent.filter { $0.hasValue }.count
        return HStack(alignment: .top, spacing: T.Space.s3) {
            VStack(alignment: .leading, spacing: 1) {
                Text(provider.today.map { Format.tokens($0.total) } ?? "—")
                    .font(.auFigure).foregroundStyle(T.Color.text.color)
                Text(provider.today == nil ? "no reading today" : "today so far")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
                Text(Presentation.seriesLabel(provider)).font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            .frame(width: 104, alignment: .leading)
            .accessibilityElement(children: .combine)
            if measuredDays >= 2 {
                DayBarChart(provider: provider, days: recent, style: .mini)
            } else {
                Text("Not enough days measured for a 7-day chart yet.")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    @ViewBuilder
    private var captions: some View {
        let usage = Presentation.usageCaption(provider, now: now)
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
            if let failed = provider.sources.first(where: { $0.status == .failed || $0.status == .auth }) {
                Label("\(failed.label): \(failed.status == .auth ? "sign-in needed" : "last check failed")",
                      systemImage: failed.status == .auth ? Symbols.signIn : Symbols.failure)
                    .foregroundStyle(T.Color.danger.color)
            }
        }
        .font(.auCaption)
        .lineLimit(2)
    }
}

// MARK: - Collect result

struct CollectResultBanner: View {
    let run: CollectRun
    let report: Report?
    let dismiss: () -> Void

    var body: some View {
        let summary = report?.collection.summary
        let title: String
        let tone: Presentation.Notice.Tone
        switch (run.outcome, report?.collection.lastAttemptResult) {
        case (.failed, _), (_, .failed?):
            title = "Check failed"; tone = .failure
        case (.partial, _), (_, .partial?):
            title = "Check partly complete"; tone = .stale
        default:
            title = "Check complete"; tone = .info
        }
        var detail = run.outcome == .failed ? run.message : ""
        if run.outcome != .failed, let summary {
            detail = "\(summary.providersUpdated) of \(summary.providersAttempted) providers fully updated"
            if summary.providersPartly > 0 { detail += " · \(summary.providersPartly) partly" }
            if summary.providersFailed > 0 { detail += " · \(summary.providersFailed) failed" }
            if !summary.skipped.isEmpty {
                detail += " · \(summary.skipped.joined(separator: ", ")) off, cached values kept"
            }
        }
        return HStack(alignment: .top) {
            NoticeBanner(notice: .init(id: "collect", tone: tone, title: title, detail: detail, providerId: nil),
                         more: 0, action: nil)
            Button(action: dismiss) { Image(systemName: "xmark").font(.auCaption) }
                .buttonStyle(.borderless)
                .accessibilityLabel("Dismiss check result")
                .padding(.top, T.Space.s2)
        }
    }
}
