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
                    try render(view.environment(store ?? defaultStore).environment(navigation), width: width, height: height, dark: dark, to: file)
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
            if !live { try historyStates(report, shoot: shoot) }
            print("snapshot: wrote \(written.count) images to \(directory.path)")
            exit(0)
        } catch {
            FileHandle.standardError.write(Data("snapshot failed: \(error)\n".utf8))
            exit(1)
        }
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
