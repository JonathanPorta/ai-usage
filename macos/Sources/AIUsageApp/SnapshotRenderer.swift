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
            let store = AppStore.preview(report, clock: report.generatedAt)
            let navigation = Navigation()
            var written: [String] = []
            func shoot<V: View>(_ name: String, width: CGFloat, height: CGFloat? = nil, _ view: V) throws {
                for dark in [false, true] {
                    let file = directory.appendingPathComponent("\(name)-\(dark ? "dark" : "light").png")
                    try render(view.environment(store).environment(navigation), width: width, height: height, dark: dark, to: file)
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
            try shoot("history-quota", width: 920, height: 1400, HistoryView(providerId: first, metric: .quota))
            print("snapshot: wrote \(written.count) images to \(directory.path)")
            exit(0)
        } catch {
            FileHandle.standardError.write(Data("snapshot failed: \(error)\n".utf8))
            exit(1)
        }
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
