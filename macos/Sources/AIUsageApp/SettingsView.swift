import AIUsageCore
import AIUsageDesign
import ServiceManagement
import SwiftUI

/// Settings: the popover is the only place settings change.
/// Collector settings are written to config.json through the collector's own
/// validated, atomic `configure`; app preferences stay in the app.
struct SettingsView: View {
    @Environment(AppStore.self) private var store
    let back: () -> Void
    let maxHeight: CGFloat

    @AppStorage(NotificationCenterBridge.limitsKey) private var notifyLimits = true
    @AppStorage(NotificationCenterBridge.failuresKey) private var notifyFailures = true
    @State private var prices: [String: String] = [:]
    /// The last value loaded from the report, to tell an in-progress edit from a stale field.
    @State private var loadedPrices: [String: String] = [:]
    @State private var loginStatus = LoginItem.status
    @State private var loginError: String?

    static let intervals: [(Int, String)] = [(900, "15 minutes"), (1800, "30 minutes"), (3600, "1 hour"),
                                             (7200, "2 hours"), (14400, "4 hours"), (43200, "12 hours")]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: back) { Label("Overview", systemImage: Symbols.back) }
                .buttonStyle(.link).font(.auBody)
                .keyboardShortcut(.cancelAction)
                .padding([.horizontal, .top], T.Space.s4)
            ScrollView {
                VStack(alignment: .leading, spacing: T.Space.s4) {
                    Text("Settings").font(.auTitle).accessibilityAddTraits(.isHeader)
                    ActionBanner()
                    if let report = store.report {
                        collectorSection(report)
                        providersSection(report)
                    }
                    notificationsSection
                    appSection
                }
                .padding(T.Space.s4)
            }
            .frame(maxHeight: maxHeight - 40)
        }
        .onExitCommand(perform: back)
        .onAppear(perform: loadPrices)
        .onChange(of: store.revision) { _, _ in loadPrices() }
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
            ForEach(report.providers) { provider in
                VStack(alignment: .leading, spacing: 4) {
                    SwitchRow(isOn: Binding(
                        get: { provider.enabled },
                        set: { value in Task { await store.applySettings([.providerEnabled(provider.id, value)]) } }
                    ), title: provider.name,
                       subtitle: provider.setup != .disabled ? Presentation.setupText(provider) : nil)
                    .disabled(store.isActing)
                    HStack(spacing: T.Space.s2) {
                        Text("Subscription $").font(.auCaption).foregroundStyle(T.Color.muted.color)
                        TextField("none", text: Binding(get: { prices[provider.id] ?? "" }, set: { prices[provider.id] = $0 }))
                            .textFieldStyle(.roundedBorder).controlSize(.small).frame(width: 70)
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
                if provider.id != report.providers.last?.id { Divider() }
            }
            Text("Turning a provider off keeps its cached data; it stops being checked. Prices are never compared with quota or reported cost.")
                .font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func parsedPrice(_ provider: Provider) -> Double?? {
        let text = (prices[provider.id] ?? "").trimmingCharacters(in: .whitespaces)
        if text.isEmpty { return .some(nil) }
        guard let value = Double(text), value >= 0, value.isFinite else { return nil }
        return .some(value)
    }

    private func priceInvalid(_ provider: Provider) -> Bool { parsedPrice(provider) == nil }

    private func priceChanged(_ provider: Provider) -> Bool {
        guard let parsed = parsedPrice(provider) else { return false }
        return parsed != provider.costs.subscription?.monthlyUsd
    }

    private func savePrice(_ provider: Provider) {
        guard let parsed = parsedPrice(provider), priceChanged(provider) else { return }
        Task { await store.applySettings([.monthlySubscription(provider.id, parsed)]) }
    }

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
    static var status: SMAppService.Status { SMAppService.mainApp.status }

    /// Returns an error message, or nil on success.
    static func set(_ enabled: Bool) -> String? {
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

    var body: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.auBody)
                if let subtitle { Text(subtitle).font(.auCaption).foregroundStyle(T.Color.muted.color) }
            }
            Spacer(minLength: T.Space.s2)
            Toggle(title, isOn: $isOn).labelsHidden().toggleStyle(.switch).controlSize(.small)
        }
        .accessibilityElement(children: .combine)
    }
}
