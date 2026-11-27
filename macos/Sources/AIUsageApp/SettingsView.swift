import AIUsageCore
import AIUsageDesign
import AppKit
import ServiceManagement
import SwiftUI

/// Settings: the popover is the only place settings change.
/// Collector settings are written to config.json through the collector's own
/// validated, atomic `configure`; app preferences stay in the app.
struct SettingsView: View {
    @Environment(AppStore.self) private var store
    let back: () -> Void
    let maxHeight: CGFloat
    /// A provider to scroll to (History's "Provider settings…").
    var focusProvider: String?

    @AppStorage(NotificationCenterBridge.limitsKey, store: AppDefaults.shared) private var notifyLimits = true
    @AppStorage(NotificationCenterBridge.failuresKey, store: AppDefaults.shared) private var notifyFailures = true
    @State private var prices: [String: String] = [:]
    /// The last value loaded from the report, to tell an in-progress edit from a stale field.
    @State private var loadedPrices: [String: String] = [:]
    @State private var loginStatus = LoginItem.status
    @State private var loginError: String?
    /// A tracking mode being saved, shown until the save ends.
    @State private var pendingTracking: [String: TrackingMode] = [:]
    /// The provider whose price field has focus; leaving it saves the edit.
    @FocusState private var focusedPrice: String?
    @State private var bannerHeight: CGFloat = 0

    static let intervals: [(Int, String)] = [(900, "15 minutes"), (1800, "30 minutes"), (3600, "1 hour"),
                                             (7200, "2 hours"), (14400, "4 hours"), (43200, "12 hours")]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: back) { Label("Overview", systemImage: Symbols.back) }
                .buttonStyle(.link).font(.auBody)
                .keyboardShortcut(.cancelAction)
                .padding([.horizontal, .top], T.Space.s4)
            // Pinned above the scrolling form, so a save failure from a lower row is seen.
            if store.action != .idle {
                ActionBanner()
                    .padding([.horizontal, .top], T.Space.s4)
                    .readHeight($bannerHeight)
            }
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: T.Space.s4) {
                        Text("Settings").font(.auTitle).accessibilityAddTraits(.isHeader)
                        if let report = store.report {
                            collectorSection(report)
                            providersSection(report)
                            cardOrderSection()
                        }
                        notificationsSection
                        appSection
                        ThemeSettingsSection(providers: store.report == nil ? [] : store.orderedProviders)
                    }
                    .padding(T.Space.s4)
                }
                .onAppear { scroll(proxy) }
                .onChange(of: focusProvider) { _, _ in scroll(proxy) }
            }
            .frame(maxHeight: maxHeight - 40 - (store.action == .idle ? 0 : bannerHeight))
        }
        .onExitCommand(perform: back)
        .onAppear(perform: loadPrices)
        .onChange(of: store.revision) { _, _ in loadPrices() }
        // A price is saved on Return, on leaving its field, on leaving Settings and
        // when the panel closes (it resigns key), not only on Return.
        .onChange(of: focusedPrice) { old, _ in
            if let old, let provider = store.report?.provider(old) { savePrice(provider) }
        }
        .onDisappear(perform: saveAllPrices)
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didResignKeyNotification)) { _ in saveAllPrices() }
    }

    private func scroll(_ proxy: ScrollViewProxy) {
        guard let focusProvider else { return }
        DispatchQueue.main.async {
            proxy.scrollTo("provider-\(focusProvider)", anchor: .top)
            focusedPrice = focusProvider
        }
    }

    /// An added account also names what it is an account of, and its id; a local
    /// plugin says it is one, and its id.
    private func subtitle(_ provider: Provider, report: Report) -> String? {
        if provider.isLocalPlugin {
            let state = report.collector.supportsLocalPlugins
                ? (provider.setup == .disabled && !provider.enabled ? "off until you turn it on" : Presentation.setupText(provider))
                : "the installed collector predates local plugins; reinstall it to collect this"
            return (["Local plugin · \(provider.id)"] + [state].compactMap { $0 }).joined(separator: " · ")
        }
        let setup = provider.setup != .disabled ? Presentation.setupText(provider) : nil
        guard provider.isAddedAccount else { return setup }
        return (["\(provider.product) account · \(provider.id)"] + [setup].compactMap { $0 }).joined(separator: " · ")
    }

    private func loadPrices() {
        guard let report = store.report else { return }
        for provider in report.providers {
            var current = ""
            if let monthly = provider.costs.subscription?.monthlyUsd {
                current = monthly == monthly.rounded() ? String(Int(monthly)) : String(monthly)
            }
            // A background refresh must not overwrite a price the user is editing.
            if prices[provider.id] == nil || prices[provider.id] == loadedPrices[provider.id] {
                prices[provider.id] = current
            }
            loadedPrices[provider.id] = current
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel(title)
            Panel { VStack(alignment: .leading, spacing: T.Space.s3) { content() } }
        }
    }

    private func collectorSection(_ report: Report) -> some View {
        section("Collector") {
            Picker("Check every", selection: Binding(
                get: { report.collector.intervalSeconds },
                set: { value in Task { await store.applySettings([.pollIntervalSeconds(value)]) } }
            )) {
                ForEach(Self.intervals + (Self.intervals.contains { $0.0 == report.collector.intervalSeconds } ? [] :
                        [(report.collector.intervalSeconds, Format.duration(Double(report.collector.intervalSeconds)))]), id: \.0) {
                    Text($0.1).tag($0.0)
                }
            }
            .disabled(store.isActing)
            Text("Saved to \(report.collector.configPath). The collector reads it before its next check; no restart needed.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func providersSection(_ report: Report) -> some View {
        section("Providers") {
            ForEach(store.orderedProviders) { provider in
                VStack(alignment: .leading, spacing: 4) {
                    if !Presentation.showsToggle(provider, in: report) {
                        // An older installed collector would never run it: nothing to turn on yet.
                        VStack(alignment: .leading, spacing: 1) {
                            Text(provider.name).font(.auBody)
                            if let subtitle = subtitle(provider, report: report) {
                                Text(subtitle).font(.auCaption).foregroundStyle(T.Color.muted.color)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .accessibilityElement(children: .combine)
                    } else if report.collector.capabilities?.trackingPolicy == true {
                        trackingRow(provider, report: report)
                    } else {
                        SwitchRow(isOn: Binding(
                            get: { provider.enabled },
                            set: { value in Task { await store.applySettings([DetailPresentation.trackingChange(
                                provider, on: value, supportsPolicy: false)]) } }
                        ), title: provider.name,
                           subtitle: subtitle(provider, report: report))
                        .disabled(store.isActing)
                    }
                    if let notice = store.detectionNotices.first(where: { $0.providerId == provider.id }) {
                        HStack(spacing: T.Space.s2) {
                            Image(systemName: "exclamationmark.triangle").foregroundStyle(T.Color.warning.color)
                                .accessibilityHidden(true)
                            Text(notice.title).font(.auCaption).fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: T.Space.s2)
                            Button(DetectionNotice.Action.keepHistory.label) { store.acknowledgeDetection(notice) }
                                .controlSize(.small)
                                .accessibilityLabel("Keep \(provider.name) history")
                        }
                    }
                    HStack(spacing: T.Space.s2) {
                        Text("Subscription $").font(.auCaption).foregroundStyle(T.Color.muted.color)
                        TextField("none", text: Binding(get: { prices[provider.id] ?? "" }, set: { prices[provider.id] = $0 }))
                            .textFieldStyle(.roundedBorder).controlSize(.small).frame(width: 70)
                            .focused($focusedPrice, equals: provider.id)
                            .onSubmit { savePrice(provider) }
                            .accessibilityLabel("\(provider.name) monthly subscription price in US dollars")
                        Text("per month · entered by you").font(.auCaption).foregroundStyle(T.Color.muted.color)
                        Spacer()
                        if priceChanged(provider) {
                            Button("Save") { savePrice(provider) }.controlSize(.small).disabled(store.isActing)
                        }
                    }
                    if priceInvalid(provider) {
                        Text("Enter a non-negative number, or leave it empty for none.").font(.auCaption)
                            .foregroundStyle(T.Color.danger.color)
                    }
                }
                .id("provider-\(provider.id)")
                if provider.id != store.orderedProviders.last?.id { Divider() }
            }
            Divider()
            AddAccountView(report: report)
        }
    }

    /// Auto / On / Off, with what detection found and what Auto is doing.
    private func trackingRow(_ provider: Provider, report: Report) -> some View {
        let saved = provider.trackingMode
        let shown = pendingTracking[provider.id] ?? saved
        // Local plugins keep an explicit choice: Auto is offered only if already saved.
        let modes: [TrackingMode] = provider.isLocalPlugin && saved != .auto ? [.on, .off] : TrackingMode.allCases
        let status = [SettingsTracking.detectionText(provider),
                      SettingsTracking.statusText(provider, shown: shown, saving: pendingTracking[provider.id] != nil)]
            .compactMap { $0 }.joined(separator: " · ")
        return HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 1) {
                Text(provider.name).font(.auBody)
                if let subtitle = subtitle(provider, report: report) {
                    Text(subtitle).font(.auCaption).foregroundStyle(T.Color.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !status.isEmpty {
                    Text(status).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
            Spacer(minLength: T.Space.s2)
            Picker("\(provider.name) tracking", selection: Binding(
                get: { shown == .unknown ? saved : shown },
                set: { mode in saveTracking(provider, mode) }
            )) {
                ForEach(modes, id: \.self) { Text(SettingsTracking.shortLabel($0)).tag($0) }
            }
            .pickerStyle(.segmented).labelsHidden().controlSize(.small).fixedSize()
            .disabled(store.isActing)
            .accessibilityValue(status.isEmpty ? shown.label : "\(shown.label), \(status)")
        }
    }

    private func saveTracking(_ provider: Provider, _ mode: TrackingMode) {
        guard mode != provider.trackingMode, !store.isActing else { return }
        pendingTracking[provider.id] = mode
        Task {
            await store.applySettings([.tracking(provider, mode)])
            // Success or failure, the report's saved mode is shown again.
            pendingTracking[provider.id] = nil
        }
    }

    private func cardOrderSection() -> some View {
        section("Card order") {
            HStack {
                Text(store.cardOrderMode == .custom ? "Custom" : "Recently used").font(.auBody)
                    .accessibilityLabel("Card order: \(store.cardOrderMode == .custom ? "Custom" : "Recently used")")
                Spacer()
                if store.cardOrderMode == .custom {
                    Button("Use recent activity order") { store.useRecentActivityOrder() }.controlSize(.small)
                }
            }
            ForEach(store.orderedProviders) { provider in
                HStack(spacing: T.Space.s2) {
                    Text(provider.name).font(.auBody)
                    if !provider.isOverviewVisible {
                        Text("Hidden").font(.auCaption).foregroundStyle(T.Color.muted.color)
                    }
                    Spacer()
                    if provider.isOverviewVisible {
                        Button { store.moveCardUp(provider.id) } label: { Image(systemName: "chevron.up") }
                            .controlSize(.small).disabled(!store.canMoveCard(provider.id, up: true))
                            .accessibilityLabel("Move \(provider.name) up").help("Move up")
                        Button { store.moveCardDown(provider.id) } label: { Image(systemName: "chevron.down") }
                            .controlSize(.small).disabled(!store.canMoveCard(provider.id, up: false))
                            .accessibilityLabel("Move \(provider.name) down").help("Move down")
                    }
                }
                .contentShape(Rectangle())
                .modifier(CardDragModifier(id: provider.id, enabled: provider.isOverviewVisible, store: store))
            }
        }
    }

    private func parsedPrice(_ provider: Provider) -> Double?? {
        ConfigChange.parsePrice(prices[provider.id] ?? "")
    }

    private func priceInvalid(_ provider: Provider) -> Bool { parsedPrice(provider) == nil }

    private func priceChanged(_ provider: Provider) -> Bool {
        guard let parsed = parsedPrice(provider) else { return false }
        return parsed != provider.costs.subscription?.monthlyUsd
    }

    private func savePrice(_ provider: Provider) {
        guard let text = prices[provider.id] else { return }
        store.savePrices([provider.id: text])
    }

    /// Every price field in one save (leaving Settings or closing the panel).
    private func saveAllPrices() { store.savePrices(prices) }

    private var notificationsSection: some View {
        section("Notifications") {
            SwitchRow(isOn: $notifyLimits, title: "When a usage limit is reached")
                .onChange(of: notifyLimits) { _, on in if on { NotificationCenterBridge.requestAuthorization() } }
            SwitchRow(isOn: $notifyFailures, title: "When a check fails or needs sign-in")
                .onChange(of: notifyFailures) { _, on in if on { NotificationCenterBridge.requestAuthorization() } }
            Text("Each limit notifies once per reset period, each failure once per check. Limits don’t badge the menu-bar icon.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var appSection: some View {
        section("This app") {
            SwitchRow(isOn: Binding(
                get: { loginStatus == .enabled || loginStatus == .requiresApproval },
                set: { on in
                    loginError = LoginItem.set(on)
                    loginStatus = LoginItem.status
                }
            ), title: "Open AI Usage at login")
            .disabled(LoginItem.mode == .unavailable)
            if loginStatus == .requiresApproval {
                Text("Approve AI Usage in System Settings › General › Login Items.").font(.auCaption)
                    .foregroundStyle(T.Color.warning.color)
            }
            if let loginError {
                Text(loginError).font(.auCaption).foregroundStyle(T.Color.danger.color)
            }
            Text("This only affects the menu app. The background collector has its own start/stop in Monitoring, and quitting this app never stops it.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
            if let report = store.report {
                Text("Collector installed: \(report.collector.installedVersion ?? "unknown") · reporting: \(report.collector.version)")
                    .font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            HStack {
                Spacer()
                Button("Quit AI Usage") { NSApplication.shared.terminate(nil) }
                    .help("Quits the menu app only; the collector keeps running.")
            }
        }
    }
}

/// The menu app's own login item (SMAppService), separate from the collector's LaunchAgent.
enum LoginItem {
    static let mode = LoginItemPolicy.mode(arguments: CommandLine.arguments,
                                           environment: ProcessInfo.processInfo.environment,
                                           bundlePath: Bundle.main.bundlePath,
                                           homeDirectory: FileManager.default.homeDirectoryForCurrentUser.path)

    static var status: SMAppService.Status {
        switch mode {
        case .managed: return SMAppService.mainApp.status
        // A fixed value keeps renders machine-independent and matches the accepted reference images.
        case .offscreenFixture: return .enabled
        case .unavailable: return .notRegistered
        }
    }

    /// Returns an error message, or nil on success.
    static func set(_ enabled: Bool) -> String? {
        guard mode == .managed else { return "Open at login is available only when AI Usage is installed in Applications." }
        do {
            if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            return nil
        } catch {
            return "Couldn’t change the login item: \(error.localizedDescription)"
        }
    }
}

/// Label on the leading edge, switch on the trailing edge.
struct SwitchRow: View {
    @Binding var isOn: Bool
    let title: String
    var subtitle: String?
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        HStack(alignment: .center) {
            // The text and the gap beside it toggle too; the switch keeps its own click,
            // so one click is one toggle.
            HStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.auBody)
                    if let subtitle { Text(subtitle).font(.auCaption).foregroundStyle(T.Color.muted.color) }
                }
                Spacer(minLength: T.Space.s2)
            }
            .contentShape(Rectangle())
            .onTapGesture { if isEnabled { isOn.toggle() } }
            Toggle(title, isOn: $isOn).labelsHidden().toggleStyle(.switch).controlSize(.small)
        }
        .accessibilityElement(children: .combine)
    }
}

/// Drag a visible Card order row onto another visible row. Uses the same Move up/down
/// steps as the buttons, so the first drag selects the custom order.
private struct CardDragModifier: ViewModifier {
    let id: String
    let enabled: Bool
    let store: AppStore

    func body(content: Content) -> some View {
        if enabled {
            content
                .draggable(id) { Text(store.cardProviders.first { $0.id == id }?.name ?? id).font(.auBody).padding(4) }
                .dropDestination(for: String.self) { items, _ in
                    guard let dragged = items.first, dragged != id else { return false }
                    let visible = store.cardProviders.map(\.id)
                    guard var from = visible.firstIndex(of: dragged), let to = visible.firstIndex(of: id) else { return false }
                    while from > to { store.moveCardUp(dragged); from -= 1 }
                    while from < to { store.moveCardDown(dragged); from += 1 }
                    return true
                }
        } else {
            content
        }
    }
}

/// Settings → Theme: what themes.json assigns, any problem with it, and the two actions.
/// Assignments are edited in the file (docs/THEMES.md); Reload applies them immediately.
struct ThemeSettingsSection: View {
    @Environment(AppStore.self) private var store
    let providers: [Provider]
    @State private var openError: String?

    var body: some View {
        let theme = store.theme
        let known = Set(providers.map(\.id))
        let elsewhere = theme.assignments.filter { !known.contains($0.key) }.sorted { $0.key < $1.key }
        let warnings = ThemeContrast.warnings(theme, defaults: T.Color.roleDefaults)
        VStack(alignment: .leading, spacing: T.Space.s2) {
            SectionLabel("Theme")
            Panel {
                VStack(alignment: .leading, spacing: T.Space.s3) {
                    row("Global", theme.globalScheme)
                    ForEach(providers) { provider in
                        row(provider.name, theme.assignments[provider.id], id: provider.id)
                    }
                    ForEach(elsewhere, id: \.key) { id, scheme in
                        row(id, scheme, note: "not in this report; applies when it returns")
                    }
                    if let diagnostic = theme.diagnostic {
                        ToneLabel(diagnostic, symbol: Symbols.attention, tone: .danger)
                            .font(.auCaption).fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(warnings, id: \.self) { warning in
                        ToneLabel(warning, symbol: Symbols.attention, tone: .warning)
                            .font(.auCaption).fixedSize(horizontal: false, vertical: true)
                    }
                    if let openError {
                        ToneLabel(openError, symbol: Symbols.failure, tone: .danger)
                            .font(.auCaption).fixedSize(horizontal: false, vertical: true)
                    }
                    Text(caption(theme))
                        .font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: T.Space.s2) {
                        Button("Open theme file") { open() }
                        Button("Reload themes") { store.reloadThemes() }
                    }
                    .controlSize(.small)
                    .disabled(store.themeFileURL == nil)
                }
            }
        }
    }

    private func row(_ title: String, _ scheme: String?, id: String? = nil, note: String? = nil) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: T.Space.s2) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.auBody)
                if let detail = note ?? id.map({ "id \($0)" }) {
                    Text(detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
            Spacer()
            Text(scheme ?? (id == nil ? "Built-in colors" : "Global")).font(.auBody)
                .foregroundStyle(scheme == nil ? T.Color.muted.color : T.Color.text.color)
        }
        .accessibilityElement(children: .combine)
    }

    private func caption(_ theme: ThemeState) -> String {
        let place = store.themeFileURL.map { $0.path } ?? "themes.json in the app’s state folder"
        return theme.fileExists
            ? "From \(place). Edit it, then choose Reload themes; no restart needed."
            : "No theme file yet: built-in colors. Open theme file creates \(place) with example schemes."
    }

    private func open() {
        do {
            guard let url = try store.prepareThemeFile() else { return }
            openError = nil
            NSWorkspace.shared.open(url)
        } catch {
            openError = "Could not create the theme file: \(error.localizedDescription)"
        }
    }
}

/// Applies the store's theme (global scheme) to a scene's whole view tree.
struct StoreTheme: ViewModifier {
    @Environment(AppStore.self) private var store
    func body(content: Content) -> some View { content.theme(store.theme) }
}
