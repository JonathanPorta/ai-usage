import AIUsageCore
import AIUsageDesign
import AppKit
import SwiftUI

/// Offscreen visual verification: renders the real views into PNGs, in light and
/// dark, at 420 pt and in a short viewport, then exits. Needs no screen-recording
/// permission. `--live` renders the live report (read-only); otherwise the fixture.
///
///     AIUsage --snapshot DIR [--live]
@MainActor
enum SnapshotRenderer {
    static func run(directory: URL, live: Bool) -> Never {
        NSApplication.shared.setActivationPolicy(.prohibited)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let report = try live ? fetchLive() : Report.fixture()
            let defaultStore = AppStore.preview(report, clock: report.generatedAt)
            let navigation = Navigation()
            var written: [String] = []
            func shoot<V: View>(_ name: String, width: CGFloat, height: CGFloat? = nil, store: AppStore? = nil, _ view: V) throws {
                for dark in [false, true] {
                    let file = directory.appendingPathComponent("\(name)-\(dark ? "dark" : "light").png")
                    try render(view.modifier(StoreTheme()).environment(store ?? defaultStore).environment(navigation), width: width, height: height, dark: dark, to: file)
                    written.append(file.lastPathComponent)
                }
            }
            let popoverWidth = T.Layout.popoverWidth
            try shoot("popover-overview", width: popoverWidth, PopoverRoot(screenHeight: 1000))
            try shoot("popover-overview-short", width: popoverWidth, PopoverRoot(screenHeight: 700))
            for provider in report.providers {
                try shoot("detail-\(provider.id)-tokens", width: popoverWidth, PopoverRoot(initialScreen: .detail(provider.id), screenHeight: 1000))
                if !provider.activeWindows.isEmpty {
                    try shoot("detail-\(provider.id)-quota", width: popoverWidth,
                              PopoverRoot(initialScreen: .detail(provider.id), detailMetric: .quota, screenHeight: 1000))
                }
            }
            try shoot("popover-monitoring", width: popoverWidth, PopoverRoot(initialScreen: .monitoring, screenHeight: 1000))
            try shoot("popover-settings", width: popoverWidth, PopoverRoot(initialScreen: .settings, screenHeight: 1000))
            let first = report.providers.first { !$0.activeWindows.isEmpty }?.id
            try shoot("history-tokens", width: 920, height: 1400, HistoryView(providerId: report.providers.first?.id))
            // A provider with an input/output split, to show the stacked output series.
            if let split = report.providers.first(where: { $0.usage.hasSplit })?.id {
                try shoot("history-tokens-\(split)", width: 920, height: 1400, HistoryView(providerId: split))
            }
            try shoot("history-quota", width: 920, height: 1400, HistoryView(providerId: first, metric: .quota))
            if !live {
                try historyStates(report, shoot: shoot)
                try accountStates(width: popoverWidth) { name, width, height, store, view in
                    try shoot(name, width: width, height: height, store: store, view)
                }
                try localPluginStates(width: popoverWidth) { name, width, height, store, view in
                    try shoot(name, width: width, height: height, store: store, view)
                }
                try overviewStates(report, width: popoverWidth) { name, width, height, store, view in
                    try shoot(name, width: width, height: height, store: store, view)
                }
                try monitoringStates(report, width: popoverWidth) { try shoot($0, width: $1, height: $2, store: $3, $4) }
                try detailStates(report, width: popoverWidth) { try shoot($0, width: $1, height: $2, store: $3, $4) }
                try allToolsStates(report) { name, width, height, store, view in
                    try shoot(name, width: width, height: height, store: store, view)
                }
                try themeStates(width: popoverWidth) { try shoot($0, width: $1, height: $2, store: $3, $4) }
            }
            print("snapshot: wrote \(written.count) images to \(directory.path)")
            exit(0)
        } catch {
            FileHandle.standardError.write(Data("snapshot failed: \(error)\n".utf8))
            exit(1)
        }
    }

    /// Settings with an added Claude account, and each "Add Claude account…" state,
    /// from the two-account fixture. Folder checks are stubbed: nothing is read from disk.
    private static func accountStates(
        width: CGFloat,
        shoot: (String, CGFloat, CGFloat?, AppStore?, AnyView) throws -> Void
    ) throws {
        let two = try Report.twoAccountFixture()
        func store(_ action: AppStore.ActionState = .idle, report: Report = two) -> AppStore {
            let store = AppStore.preview(report, clock: report.generatedAt)
            store.previewAction(action)
            return store
        }
        func form(_ view: some View) -> AnyView {
            AnyView(view.padding(T.Space.s4).background(T.Color.background.color))
        }
        let folder = URL(fileURLWithPath: "/Users/you/.claude-personal")
        let ready = AccountDraft(label: "Claude (personal)", id: "claude-personal", folder: folder)
        try shoot("popover-settings-two-accounts", width, nil, store(), AnyView(PopoverRoot(initialScreen: .settings, screenHeight: 1600)))
        try shoot("popover-overview-two-accounts", width, nil, store(), AnyView(PopoverRoot(screenHeight: 1200)))
        try shoot("detail-claude-az-quota", width, nil, store(),
                  AnyView(PopoverRoot(initialScreen: .detail("claude-az"), detailMetric: .quota, screenHeight: 1000)))
        var older = two
        older.collector.installedVersion = "2.2.0"
        older.collector.capabilities?.instances = nil
        try shoot("add-account-unsupported", width, nil, store(report: older), form(AddAccountView(report: older)))
        try shoot("add-account-closed", width, nil, store(), form(AddAccountView(report: two)))
        try shoot("add-account-empty", width, nil, store(), form(AddAccountView(report: two, open: true)))
        try shoot("add-account-problems", width, nil, store(), form(AddAccountView(
            report: two, open: true, draft: AccountDraft(label: "Claude (personal)", id: "claude", idEdited: true, folder: folder),
            touched: true, folderCheck: { _ in false })))
        try shoot("add-account-duplicate", width, nil, store(), form(AddAccountView(
            report: two, open: true, draft: AccountDraft(label: "Claude (work)", id: "claude-az", idEdited: true, folder: folder),
            touched: true, folderCheck: { _ in true })))
        try shoot("add-account-ready", width, nil, store(), form(AddAccountView(
            report: two, open: true, draft: ready, touched: true, folderCheck: { _ in true })))
        try shoot("add-account-saving", width, nil, store(.running("Adding Claude (personal)…")), form(AddAccountView(
            report: two, open: true, draft: ready, touched: true, folderCheck: { _ in true })))
        try shoot("add-account-saved", width, nil, store(.done("Claude (personal) added. It is read at the next check.")),
                  form(VStack(alignment: .leading, spacing: T.Space.s3) { ActionBanner(); AddAccountView(report: two) }))
        let refusal = "added accounts need the installed collector 2.3.0 or newer (installed: 2.2.0); reinstall it first with `python3 ai_usage_service.py install`"
        try shoot("add-account-failed", width, nil, store(.failed(refusal)),
                  form(AddAccountView(report: two, open: true, draft: ready, touched: true, failure: refusal, folderCheck: { _ in true })))
    }

    /// A synthetic local plugin ("Example", from the local-plugin fixture): the overview,
    /// its detail, and Settings when on, when not turned on yet, and with an installed
    /// collector that predates local plugins (no switch).
    private static func localPluginStates(
        width: CGFloat,
        shoot: (String, CGFloat, CGFloat?, AppStore?, AnyView) throws -> Void
    ) throws {
        let local = try Report.localPluginFixture()
        func store(_ report: Report) -> AppStore { AppStore.preview(report, clock: report.generatedAt) }
        func variant(_ change: (inout Report, Int) -> Void) -> Report {
            var copy = local
            if let index = copy.providers.firstIndex(where: { $0.id == "example" }) { change(&copy, index) }
            return copy
        }
        try shoot("popover-overview-local-plugin", width, nil, store(local), AnyView(PopoverRoot(screenHeight: 1200)))
        // The whole list, down to the Example card that the capped popover scrolls to.
        try shoot("popover-overview-local-plugin-full", width, nil, store(local),
                  AnyView(PopoverRoot(screenHeight: 2400, uncapped: true)))
        try shoot("detail-example-tokens", width, nil, store(local),
                  AnyView(PopoverRoot(initialScreen: .detail("example"), screenHeight: 1000)))
        try shoot("detail-example-quota", width, nil, store(local),
                  AnyView(PopoverRoot(initialScreen: .detail("example"), detailMetric: .quota, screenHeight: 1000)))
        try shoot("popover-settings-local-plugin", width, nil, store(local),
                  AnyView(PopoverRoot(initialScreen: .settings, screenHeight: 1600)))
        let notOn = variant { report, index in
            report.providers[index].enabled = false
            report.providers[index].setup = .disabled
            report.providers[index].setupNote = "Not turned on. Turn it on in Settings, or run `collector.py plugin enable example`."
        }
        try shoot("popover-settings-local-plugin-off", width, nil, store(notOn),
                  AnyView(PopoverRoot(initialScreen: .settings, screenHeight: 1600)))
        let older = variant { report, index in
            report.collector.installedVersion = "2.3.0"
            report.collector.capabilities?.externalPlugins = false
            report.providers[index].setup = .notConfigured
            report.providers[index].setupNote = "Not collected: the installed collector (2.3.0) predates local plugins. Reinstall the collector to collect it."
        }
        try shoot("popover-settings-local-plugin-unsupported", width, nil, store(older),
                  AnyView(PopoverRoot(initialScreen: .settings, screenHeight: 1600)))
    }

    /// History states for layout review: a narrower window (the shared compact
    /// breakpoint), 90 days, and each empty state. The empty states use fixture
    /// variants derived here, so the committed fixture stays unchanged.
    private static func historyStates(
        _ report: Report,
        shoot: (String, CGFloat, CGFloat?, AppStore?, HistoryView) throws -> Void
    ) throws {
        let narrow: CGFloat = 760
        let codex = report.providers.first { !$0.activeWindows.isEmpty }?.id
        try shoot("history-tokens-90d", 920, 1400, nil, HistoryView(providerId: codex, tokenRange: 90))
        try shoot("history-tokens-narrow", narrow, 1400, nil, HistoryView(providerId: codex))
        try shoot("history-tokens-claude-narrow", narrow, 1400, nil, HistoryView(providerId: "claude"))
        try shoot("history-quota-narrow", narrow, 1400, nil, HistoryView(providerId: codex, metric: .quota))
        try shoot("history-quota-grok", 920, 1400, nil, HistoryView(providerId: "grok", metric: .quota))
        try shoot("history-empty-not-detected", 920, 900, nil, HistoryView(providerId: "gemini_cli"))
        try shoot("history-empty-not-detected-narrow", narrow, 900, nil, HistoryView(providerId: "gemini_cli"))

        func variant(_ id: String, _ change: (inout Provider) -> Void) -> AppStore {
            var copy = report
            if let index = copy.providers.firstIndex(where: { $0.id == id }) { change(&copy.providers[index]) }
            return AppStore.preview(copy, clock: report.generatedAt)
        }
        func clearDays(_ provider: inout Provider, keepingFirst keep: Int = 0) {
            for index in provider.days.indices where index >= keep {
                provider.days[index].state = .missing
                provider.days[index].total = nil
                provider.days[index].input = nil
                provider.days[index].output = nil
                provider.days[index].cacheRead = nil
            }
        }
        let disabled = variant("antigravity") { provider in
            clearDays(&provider)
            provider.enabled = false
            provider.setup = .disabled
            provider.setupNote = nil
        }
        try shoot("history-empty-tracking-off", 920, 900, disabled, HistoryView(providerId: "antigravity"))
        let noUsage = variant("antigravity") { provider in clearDays(&provider) }
        try shoot("history-empty-no-usage", 920, 900, noUsage, HistoryView(providerId: "antigravity"))
        // Old history only: the first 30 of 90 days keep their readings.
        let oldOnly = variant("grok") { provider in
            if let first = provider.days.firstIndex(where: \.hasValue) {
                clearDays(&provider, keepingFirst: max(first + 1, 30))
            }
        }
        try shoot("history-empty-none-in-range", 920, 900, oldOnly, HistoryView(providerId: "grok", tokenRange: 7))
    }

    /// History's All tools page (phase 4): two Claude Code accounts with the other tools (wide,
    /// narrow, 90 days), an incomplete day with an account that is off (that day selected),
    /// five accounts of one tool (also narrow, with today selected), only one-account tools,
    /// and no usage at all.
    private static func allToolsStates(
        _ single: Report,
        shoot: (String, CGFloat, CGFloat?, AppStore?, AnyView) throws -> Void
    ) throws {
        func store(_ report: Report) -> AppStore { AppStore.preview(report, clock: report.generatedAt) }
        func page(_ range: Int = 30) -> HistoryView { HistoryView(allTools: true, allRange: range) }
        let two = try Report.twoAccountFixture()
        let height: CGFloat = 2300
        try shoot("history-all-tools", 920, height, store(two), AnyView(page()))
        try shoot("history-all-tools-narrow", 760, height, store(two), AnyView(page()))
        try shoot("history-all-tools-90d", 920, height, store(two), AnyView(page(90)))
        let incomplete = try Report.scenarioFixture("all-tools")
        let day = incomplete.groups?.first { $0.type == "claude" }?.days.first { $0.state == .incomplete }?.date
        try shoot("history-all-tools-incomplete-off", 920, height, store(incomplete),
                  AnyView(page().environment(\.chartPreviewDate, day)))
        let overflow = try Report.scenarioFixture("overflow")
        try shoot("history-all-tools-overflow", 920, height, store(overflow), AnyView(page()))
        // Today selected at the narrow width: five accounts, two with long names and each with
        // its own time, so the readout runs past three lines and must not be cut off.
        try shoot("history-all-tools-overflow-narrow", 760, 2900, store(overflow),
                  AnyView(page().environment(\.chartPreviewDate, overflow.today)))
        try shoot("history-all-tools-single", 920, height, store(single), AnyView(page()))
        var empty = two
        for index in empty.providers.indices {
            for day in empty.providers[index].days.indices where empty.providers[index].days[day].state != .notCollected {
                empty.providers[index].days[day].state = .missing
                empty.providers[index].days[day].total = nil
                empty.providers[index].days[day].input = nil
                empty.providers[index].days[day].output = nil
                empty.providers[index].days[day].cacheRead = nil
            }
        }
        empty.groups = empty.groups?.map { group in
            var group = group
            group.days = group.days.map { day in
                var day = day
                guard day.state != .notCollected else { return day }
                day.state = .missing
                day.total = nil
                day.incompleteEvents = 0
                day.parts = day.parts.map { part in
                    var part = part
                    if part.state != .notCollected { (part.state, part.total, part.asOf) = (.missing, nil, nil) }
                    return part
                }
                return day
            }
            return group
        }
        try shoot("history-all-tools-no-usage", 920, 1500, store(empty), AnyView(page()))
    }

    /// Slice 2 overview states and slice 2 Settings states, from fixture variants built here.
    private static func overviewStates(
        _ report: Report, width: CGFloat,
        shoot: (String, CGFloat, CGFloat?, AppStore?, AnyView) throws -> Void
    ) throws {
        func store(_ report: Report) -> AppStore { AppStore.preview(report, clock: report.generatedAt) }
        func variant(_ change: (inout Report) -> Void) -> Report { var copy = report; change(&copy); return copy }
        func index(_ r: Report, _ id: String) -> Int { r.providers.firstIndex { $0.id == id }! }
        func overview(_ height: CGFloat = 1200) -> AnyView { AnyView(PopoverRoot(screenHeight: height)) }
        func settings() -> AnyView { AnyView(PopoverRoot(initialScreen: .settings, screenHeight: 2400, uncapped: true)) }
        func form(_ view: some View) -> AnyView { AnyView(view.padding(T.Space.s4).background(T.Color.background.color)) }

        let oneOff = variant { r in
            let i = index(r, "antigravity")
            r.providers[i].tracking = .off; r.providers[i].trackingEffective = .inactive; r.providers[i].enabled = false
        }
        try shoot("popover-overview-one-off", width, nil, store(oneOff), overview())
        let allInactive = variant { r in
            for i in r.providers.indices { r.providers[i].tracking = .auto; r.providers[i].trackingEffective = .inactive }
        }
        try shoot("popover-overview-choose-providers", width, nil, store(allInactive), overview())
        let awaiting = variant { r in
            for i in r.providers.indices { r.providers[i].tracking = .auto; r.providers[i].trackingEffective = .pending }
        }
        try shoot("popover-overview-awaiting-detection", width, nil, store(awaiting), overview())
        // Stale + partial failure: the result indicator carries a warning badge.
        let stale = variant { r in
            r.collection.lastAttemptResult = .partial
            let i = index(r, "claude")
            if var failure = r.collection.failures.first {
                failure.provider = "claude"; failure.source = "subscription-usage"; failure.message = "usage endpoint returned 503"
                r.collection.failures.append(failure)
            }
            // The failed check marks its source failed, as the collector reports it, so the
            // per-provider breakdown and the issues list describe the same failures.
            for s in r.providers[i].sources.indices where r.providers[i].sources[s].id == "subscription-usage" {
                r.providers[i].sources[s].status = .failed
            }
            r.providers[i].quota.windows[0].status = .stale
            r.providers[i].quota.windows[0].staleCause = .failed
        }
        let staleStore = store(stale)
        try shoot("popover-overview-result-warning", width, nil, staleStore, overview())
        let issues = OverviewIssues(report: staleStore.report, now: stale.generatedAt, detections: staleStore.detectionNotices,
                                    refreshError: nil, collect: .idle)
        try shoot("overview-collection-details", 352, nil, staleStore, form(CollectionDetailsView(
            issues: issues, now: stale.generatedAt, openDetail: { _ in }, openMonitoring: {}, openSettings: {},
            acknowledge: { _ in })))
        // A clean, successful check while Codex sits at its weekly limit: the result stays a success.
        let limitOnly = variant { r in
            r.collection.lastAttemptResult = .success
            r.collection.failures = []
            if let expected = r.collection.summary?.providersExpected {
                r.collection.summary?.providersFresh = expected
                r.collection.summary?.providersStale = 0
                r.collection.summary?.providersPartly = 0
                r.collection.summary?.providersFailed = 0
            }
            for i in r.providers.indices {
                for s in r.providers[i].sources.indices where r.providers[i].sources[s].status != .ok {
                    r.providers[i].sources[s].status = .ok
                }
                for w in r.providers[i].quota.windows.indices {
                    r.providers[i].quota.windows[w].status = .current
                    r.providers[i].quota.windows[w].staleCause = nil
                    r.providers[i].quota.windows[w].omitted = false
                }
                if r.providers[i].quota.status == .mixed || r.providers[i].quota.status == .stale { r.providers[i].quota.status = .current }
                r.providers[i].usage.staleCause = nil
            }
        }
        // Codex first, so the at-limit card is in view rather than below the fold.
        let limitStore = store(limitOnly)
        for _ in 0..<4 { limitStore.moveCardUp("codex") }
        try shoot("popover-overview-success-at-limit", width, nil, limitStore, overview())
        // Custom order: Codex moved to the top.
        let custom = store(report)
        for _ in 0..<4 { custom.moveCardUp("codex") }
        try shoot("popover-overview-custom-order", width, nil, custom, overview())
        try shoot("popover-overview-recent-order", width, nil, store(report), overview())
        // Long name with many quota windows.
        let long = variant { r in
            let i = index(r, "claude")
            r.providers[i].name = "Claude Code (work account for the platform infrastructure team)"
            var extra = r.providers[i].quota.windows
            for (n, w) in r.providers[i].quota.windows.enumerated() {
                var copy = w; copy.id += "-x\(n)"; copy.label += " · Opus"; extra.append(copy)
            }
            r.providers[i].quota.windows = extra
        }
        try shoot("popover-overview-long-name", width, nil, store(long), AnyView(PopoverRoot(screenHeight: 2400, uncapped: true)))
        // Statistics popover content.
        for id in ["claude", "codex"] {
            let provider = report.providers[index(report, id)]
            try shoot("overview-statistics-\(id)", 312, nil, nil, form(ProviderStatisticsView(
                name: provider.name, stats: CardStatistics.statistics(provider, now: report.generatedAt))))
        }
        // A reset already past, and an unknown reset.
        let resets = variant { r in
            let i = index(r, "claude")
            r.providers[i].quota.windows[0].resetsAt = r.generatedAt.addingTimeInterval(-600)
            r.providers[i].quota.windows[1].resetsAt = nil
        }
        try shoot("popover-overview-reset-past-unknown", width, nil, store(resets), overview())

        // Settings: Auto + CLI not found + saved data (the detection notice row).
        let notFound = variant { r in
            let i = index(r, "antigravity")
            r.providers[i].tracking = .auto
            r.providers[i].trackingEffective = .inactive
            r.providers[i].detection = Detection(installed: false, requirement: .executable, savedData: true)
        }
        try shoot("popover-settings-detection-notice", width, nil, store(notFound), settings())
        // Settings in Custom order, with a hidden (Off) provider.
        let customSettings = store(oneOff)
        customSettings.moveCardUp("codex")
        try shoot("popover-settings-custom-order", width, nil, customSettings, settings())
        // An older report without the tracking-policy capability: the on/off switch.
        let older = variant { r in
            r.collector.capabilities?.trackingPolicy = nil
            for i in r.providers.indices {
                r.providers[i].tracking = nil; r.providers[i].trackingEffective = nil; r.providers[i].detection = nil
            }
        }
        try shoot("popover-settings-no-tracking-policy", width, nil, store(older), settings())
    }

    /// Slice 3 Monitoring states, from fixture variants built here.
    private static func monitoringStates(
        _ report: Report, width: CGFloat,
        shoot: (String, CGFloat, CGFloat?, AppStore?, AnyView) throws -> Void
    ) throws {
        func store(_ report: Report) -> AppStore { AppStore.preview(report, clock: report.generatedAt) }
        func variant(_ change: (inout Report) -> Void) -> Report { var copy = report; change(&copy); return copy }
        func page() -> AnyView { AnyView(PopoverRoot(initialScreen: .monitoring, screenHeight: 2400, uncapped: true)) }
        func success(_ r: inout Report) {
            r.collection.lastAttemptResult = .success
            r.collection.lastSuccessAt = r.collection.lastAttemptAt
            r.collection.failures = []
            if let expected = r.collection.summary?.providersExpected {
                r.collection.summary?.providersFresh = expected
                r.collection.summary?.providersStale = 0
                r.collection.summary?.providersPartly = 0
                r.collection.summary?.providersFailed = 0
            }
            for i in r.providers.indices {
                // Every source healthy, so the "updated" summary is truthful.
                for s in r.providers[i].sources.indices { r.providers[i].sources[s].status = .ok }
            }
        }
        let paused = variant { r in success(&r); r.schedule.state = "paused"; r.schedule.nextScheduledAt = nil }
        try shoot("popover-monitoring-paused", width, nil, store(paused), page())
        let stopped = variant { r in
            success(&r); r.service.state = .stopped; r.service.pid = nil; r.service.disabled = true
        }
        try shoot("popover-monitoring-stopped", width, nil, store(stopped), page())
        // Running, with Grok's billing failing and Claude's daily stats stale (the fixture), an hour after the last success.
        let partial = variant { r in
            r.collection.lastSuccessAt = r.collection.lastAttemptAt?.addingTimeInterval(-3600)
        }
        try shoot("popover-monitoring-partial", width, nil, store(partial), page())
        let inactive = variant { r in
            for id in ["antigravity", "gemini_cli"] {
                guard let i = r.providers.firstIndex(where: { $0.id == id }) else { continue }
                r.providers[i].tracking = .off; r.providers[i].trackingEffective = .inactive; r.providers[i].enabled = false
                for s in r.providers[i].sources.indices { r.providers[i].sources[s].status = .off }
            }
            r.collection.summary?.providersExpected = 3
        }
        try shoot("popover-monitoring-inactive-expanded", width, nil, store(inactive), AnyView(
            MonitoringView(back: {}, openDetail: { _ in }, maxHeight: 2400, collectionDetails: true, inactiveExpanded: true)
                .frame(width: width).background(T.Color.background.color)))
        let none = variant { r in
            for i in r.providers.indices {
                r.providers[i].tracking = .off; r.providers[i].trackingEffective = .inactive; r.providers[i].enabled = false
            }
            r.collection.summary?.providersExpected = 0
            r.collection.summary?.providersFresh = 0
            r.collection.failures = []
        }
        try shoot("popover-monitoring-no-active", width, nil, store(none), page())
    }

    /// Slice 3 provider detail empty states, the expanded chart key, and History
    /// opened from a provider's detail, from fixture variants built here.
    private static func detailStates(
        _ report: Report, width: CGFloat,
        shoot: (String, CGFloat, CGFloat?, AppStore?, AnyView) throws -> Void
    ) throws {
        func store(_ report: Report) -> AppStore { AppStore.preview(report, clock: report.generatedAt) }
        func variant(_ id: String, _ change: (inout Provider) -> Void) -> Report {
            var copy = report
            if let i = copy.providers.firstIndex(where: { $0.id == id }) { change(&copy.providers[i]) }
            return copy
        }
        func clearDays(_ provider: inout Provider, keepingFirst keep: Int = 0) {
            for i in provider.days.indices where i >= keep && provider.days[i].state != .notCollected {
                provider.days[i].state = .missing
                provider.days[i].total = nil; provider.days[i].input = nil
                provider.days[i].output = nil; provider.days[i].cacheRead = nil
            }
        }
        func detail(_ id: String, metric: DetailMetric = .tokens, range: Int = 7, key: Bool = false) -> AnyView {
            AnyView(DetailSnapshotHost(providerId: id, metric: metric, range: range, chartKeyOpen: key))
        }
        let off = variant("antigravity") { p in p.tracking = .off; p.trackingEffective = .inactive; p.enabled = false }
        try shoot("detail-tracking-off", width, nil, store(off), detail("antigravity"))
        let auto = variant("codex") { p in p.tracking = .auto; p.trackingEffective = .active }
        try shoot("detail-tracking-auto", width, nil, store(auto), detail("codex"))
        let never = variant("antigravity") { p in clearDays(&p); p.today = nil; p.modelsToday = [] }
        try shoot("detail-no-usage", width, nil, store(never), detail("antigravity"))
        // No reading today: today's day entry is missing too, as a real report (or a rollover) has it.
        let noToday = variant("codex") { p in
            p.today = nil; p.modelsToday = []
            for i in p.days.indices where p.days[i].date == report.today {
                p.days[i].state = .missing
                p.days[i].total = nil; p.days[i].input = nil; p.days[i].output = nil; p.days[i].cacheRead = nil
                p.days[i].cacheWrite = nil; p.days[i].asOf = nil
            }
        }
        try shoot("detail-no-reading-today", width, nil, store(noToday), detail("codex"))
        let oldOnly = variant("grok") { p in
            if let first = p.days.firstIndex(where: \.hasValue) { clearDays(&p, keepingFirst: max(first + 1, 30)) }
            p.today = nil; p.modelsToday = []
        }
        try shoot("detail-no-data-in-range", width, nil, store(oldOnly), detail("grok"))
        try shoot("detail-no-data-in-range-30d", width, nil, store(oldOnly), detail("grok", range: 30))
        let noPrice = variant("claude") { p in p.costs.subscription = nil }
        try shoot("detail-missing-price", width, nil, store(noPrice), detail("claude"))
        let noCost = variant("codex") { p in p.costs.reported = nil; p.costs.reportedNote = nil }
        try shoot("detail-cost-not-reported", width, nil, store(noCost), detail("codex"))
        try shoot("detail-chart-key-expanded", width, nil, nil, detail("claude", key: true))
        if let quota = report.providers.first(where: { !$0.activeWindows.isEmpty }) {
            try shoot("detail-chart-key-expanded-quota", width, nil, nil, detail(quota.id, metric: .quota, key: true))
            // History as opened from that provider's detail on Quota: provider, metric, window and range kept.
            let window = quota.activeWindows.last
            let context = DetailPresentation.historyContext(quota, metric: .quota, tokenRange: 7,
                                                            windowId: window?.id, quotaRange: window?.ranges.last)
            try shoot("history-from-detail", 920, 1400, nil, AnyView(HistoryView(
                providerId: context.providerId, metric: context.metric, tokenRange: context.tokenRange,
                windowId: context.windowId, quotaRange: context.quotaRange)))
        }
    }

    /// File-defined themes, from in-memory documents parsed by the real parser (no theme
    /// file is read; the invalid-file case writes only to a fresh temporary directory).
    private static func themeStates(
        width: CGFloat,
        shoot: (String, CGFloat, CGFloat?, AppStore?, AnyView) throws -> Void
    ) throws {
        let two = try Report.twoAccountFixture()
        let schemes = """
        "schemes": {
          "slate": {
            "light": { "background": "#EEF1F4", "panel": "#FBFCFD", "line": "#D3D9E0", "accent": "#1769AA",
                       "accentBackground": "#E1ECF7", "series1": "#1769AA", "series2": "#7A4FB5" },
            "dark": { "background": "#1E2227", "panel": "#2A2F36", "line": "#3E454E", "accent": "#80B7F7",
                      "accentBackground": "#22364D", "series1": "#80B7F7", "series2": "#C9B4F5" }
          },
          "warm": {
            "light": { "accent": "#9A4A24", "accentSoft": "#B9714D", "accentBackground": "#F8EAE1", "panel": "#FFFBF7" },
            "dark": { "accent": "#EAAF86", "accentSoft": "#C98A62", "accentBackground": "#45302A", "panel": "#352E2A" }
          },
          "violet": {
            "light": { "accent": "#7245B8", "accentSoft": "#8F6BCB", "accentBackground": "#F1EBFA", "panel": "#FCFAFF" },
            "dark": { "accent": "#B39AEF", "accentSoft": "#9479D6", "accentBackground": "#342A4A", "panel": "#312D3A" }
          }
        }
        """
        func state(_ json: String) throws -> ThemeState {
            switch ThemeParser.parse(Data(json.utf8)) {
            case let .success(document): return ThemeState(document: document, fileExists: true)
            case let .failure(error): throw CocoaError(.coderInvalidValue, userInfo: [NSDebugDescriptionErrorKey: "\(error)"])
            }
        }
        let global = try state(#"{"version": 1, "global": "slate", \#(schemes)}"#)
        let perProvider = try state(#"{"version": 1, "global": "slate", "providers": {"claude": "warm", "claude-az": "violet", "retired-account": "warm"}, \#(schemes)}"#)
        func store(_ report: Report, _ theme: ThemeState) -> AppStore {
            let store = AppStore.preview(report, clock: report.generatedAt)
            store.previewTheme(theme)
            return store
        }
        try shoot("theme-overview-global", width, nil, store(two, global), AnyView(PopoverRoot(screenHeight: 1200)))
        try shoot("theme-overview-providers", width, nil, store(two, perProvider), AnyView(PopoverRoot(screenHeight: 1200)))
        try shoot("theme-detail-provider", width, nil, store(two, perProvider),
                  AnyView(PopoverRoot(initialScreen: .detail("claude-az"), detailMetric: .quota, screenHeight: 1000)))
        try shoot("theme-detail-provider-tokens", width, nil, store(two, perProvider),
                  AnyView(PopoverRoot(initialScreen: .detail("claude"), screenHeight: 1000)))
        try shoot("theme-history-provider", 920, 1400, store(two, perProvider), AnyView(HistoryView(providerId: "claude")))
        try shoot("theme-history-all-tools", 920, 2300, store(two, perProvider), AnyView(HistoryView(allTools: true, allRange: 30)))
        try shoot("theme-settings", width, nil, store(two, perProvider),
                  AnyView(SettingsView(back: {}, maxHeight: 4000).background(T.Color.background.color)))
        // A bad edit (a malformed color) after a valid file: the last valid theme stays, with one diagnostic.
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("ai-usage-theme-snapshot-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: folder) }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent(ThemeLoader.fileName)
        try Data(##"{"version": 1, "global": "slate", "schemes": {"slate": {"light": {"panel": "#F3F5F"}}}}"##.utf8).write(to: file)
        let invalid = ThemeLoader.load(file, previous: perProvider)
        try shoot("theme-settings-invalid", width, nil, store(two, invalid),
                  AnyView(SettingsView(back: {}, maxHeight: 4000).background(T.Color.background.color)))
        try shoot("theme-overview-invalid", width, nil, store(two, invalid), AnyView(PopoverRoot(screenHeight: 1200)))
    }

    private static func fetchLive() throws -> Report {
        let environment = CollectorEnvironment.resolve()
        let client = LiveReportClient(environment: environment)
        var result: Result<Report, Error>?
        Task.detached { 
            do { result = .success(try await client.fetch()) } catch { result = .failure(error) }
        }
        while result == nil { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
        return try result!.get()
    }

    private static func render<V: View>(_ view: V, width: CGFloat, height: CGFloat?, dark: Bool, to file: URL) throws {
        let appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        let host = NSHostingView(rootView: AnyView(view.frame(width: width)))
        host.appearance = appearance
        let fitting = host.fittingSize
        let size = CGSize(width: width, height: height ?? fitting.height)
        let window = NSWindow(contentRect: CGRect(origin: .zero, size: size), styleMask: [.borderless],
                              backing: .buffered, defer: false)
        window.appearance = appearance
        window.contentView = host
        host.frame = CGRect(origin: .zero, size: size)
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.4))
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { throw CocoaError(.fileWriteUnknown) }
        host.cacheDisplay(in: host.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { throw CocoaError(.fileWriteUnknown) }
        try png.write(to: file)
    }
}

/// Provider detail rendered at its full height (no popover cap), for snapshots.
private struct DetailSnapshotHost: View {
    @Environment(AppStore.self) private var store
    let providerId: String
    let metric: DetailMetric
    let range: Int
    let chartKeyOpen: Bool

    var body: some View {
        if let provider = store.report?.provider(providerId) {
            ProviderDetailView(provider: provider, back: {}, openHistory: { _ in }, maxHeight: 2400,
                               initialMetric: metric, tokenRange: range, chartKeyOpen: chartKeyOpen)
                .fixedSize(horizontal: false, vertical: true)
                .background(T.Color.background.color)
                .themeScope(providerId)
        }
    }
}
