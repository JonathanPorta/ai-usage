import AIUsageCore
import AIUsageDesign
import AppKit
import SwiftUI

/// Navigation shared by the popover and the History window (which provider
/// History should preselect). Data lives in AppStore; this is UI state only.
@MainActor
@Observable
final class Navigation {
    var historyRequest: HistoryRequest?
}

struct HistoryRequest: Equatable {
    var providerId: String?
    var nonce = UUID()
}

@main
struct AIUsageApp: App {
    /// The one shared store: both scenes render from it.
    private let store: AppStore
    private let navigation = Navigation()

    init() {
        if let index = CommandLine.arguments.firstIndex(of: "--snapshot"), index + 1 < CommandLine.arguments.count {
            SnapshotRenderer.run(directory: URL(fileURLWithPath: CommandLine.arguments[index + 1]),
                                 live: CommandLine.arguments.contains("--live"))
        }
        if CommandLine.arguments.contains("--measure") { MeasureHarness.run() }
        let store = AppStore.live()
        store.onSnapshotChange = { report in NotificationCenterBridge.handle(report, now: Date()) }
        store.start()
        self.store = store
    }

    var body: some Scene {
        MenuBarExtra {
            PopoverRoot()
                .environment(store)
                .environment(navigation)
        } label: {
            MenuBarLabel(store: store)
        }
        .menuBarExtraStyle(.window)

        Window("AI Usage History", id: HistoryView.windowId) {
            HistoryView()
                .environment(store)
                .environment(navigation)
                .frame(minWidth: T.Layout.historyMinWidth, minHeight: T.Layout.historyMinHeight)
        }
        .defaultSize(width: 920, height: 680)
        .windowResizability(.contentMinSize)
    }
}

/// Menu-bar glyph: a template `waveform.path.ecg` with a state badge.
struct MenuBarLabel: View {
    let store: AppStore

    var body: some View {
        Image(nsImage: GlyphRenderer.image(badge: badge, dimmed: dimmed))
            .accessibilityLabel("AI Usage")
    }

    private var badge: String? {
        if store.isCollecting { return Symbols.Badge.collecting }
        guard let report = store.report else { return nil }
        switch report.service.state {
        case .stopped, .notInstalled: return Symbols.Badge.stopped
        default: break
        }
        if report.schedule.isPaused { return Symbols.Badge.paused }
        if report.collection.lastAttemptResult == .failed || !report.collection.failures.isEmpty { return Symbols.Badge.attention }
        return nil
    }

    private var dimmed: Bool {
        guard let state = store.report?.service.state else { return false }
        return state == .stopped || state == .notInstalled
    }
}

enum GlyphRenderer {
    static func image(badge: String?, dimmed: Bool) -> NSImage {
        let size = NSSize(width: 22, height: 16)
        let configuration = NSImage.SymbolConfiguration(pointSize: 13, weight: .regular)
        let base = NSImage(systemSymbolName: Symbols.appMark, accessibilityDescription: "AI Usage")?
            .withSymbolConfiguration(configuration)
        let image = NSImage(size: size, flipped: false) { rect in
            if let base {
                let origin = NSPoint(x: 0, y: (rect.height - base.size.height) / 2)
                base.draw(at: origin, from: .zero, operation: .sourceOver, fraction: dimmed ? 0.45 : 1)
            }
            if let badge, let mark = NSImage(systemSymbolName: badge, accessibilityDescription: nil)?
                .withSymbolConfiguration(.init(pointSize: 8, weight: .bold)) {
                let point = NSPoint(x: rect.width - mark.size.width, y: 0)
                NSColor.clear.set()
                mark.draw(at: point, from: .zero, operation: .sourceOver, fraction: 1)
            }
            return true
        }
        image.isTemplate = true
        return image
    }
}
