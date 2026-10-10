import AIUsageCore
import AppKit
import Foundation

/// `AIUsage --measure`: interaction latency against the live (read-only) report.
/// Set AI_USAGE_STATE_DIR to keep the measurement cache out of the app's own.
@MainActor
enum MeasureHarness {
    static func run() -> Never {
        NSApplication.shared.setActivationPolicy(.prohibited)
        var results: [String: Any] = [:]
        func wait(until condition: @escaping () -> Bool, timeout: TimeInterval = 120) {
            let deadline = Date().addingTimeInterval(timeout)
            while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.005)) }
        }

        // 1. Cold refresh (no cache): one full report run.
        let cold = AppStore.live()
        var started = Date()
        cold.start()
        wait { cold.reportRuns >= 1 && !cold.isRefreshing }
        results["cold_refresh_seconds"] = Date().timeIntervalSince(started)
        results["csv_rows"] = cold.snapshot?.collector.csvRows

        // 2. Warm launch: the cached snapshot renders before any scan finishes.
        let warm = AppStore.live()
        started = Date()
        warm.start()
        results["first_render_from_cache_ms"] = warm.report == nil ? nil : Date().timeIntervalSince(started) * 1000
        results["first_render_is_cached"] = warm.isCachedReport

        // 3. Main-thread responsiveness while that background refresh runs.
        var maxGap: TimeInterval = 0
        var last = Date()
        let ticker = Timer.scheduledTimer(withTimeInterval: 0.01, repeats: true) { _ in
            let now = Date()
            maxGap = max(maxGap, now.timeIntervalSince(last))
            last = now
        }
        started = Date()
        wait { warm.reportRuns >= 1 && !warm.isRefreshing }
        ticker.invalidate()
        results["background_refresh_seconds"] = Date().timeIntervalSince(started)
        results["max_main_thread_gap_ms"] = maxGap * 1000

        // 4. Reopening the popover with unchanged files: no scans.
        let runsBefore = warm.reportRuns
        for _ in 0..<10 { warm.popoverOpened() }
        RunLoop.main.run(until: Date().addingTimeInterval(1.5))
        results["scans_for_10_reopens"] = warm.reportRuns - runsBefore

        // 5. Five overlapping refresh requests: one run, plus at most one follow-up.
        let runsBeforeBurst = warm.reportRuns
        for _ in 0..<5 { Task { await warm.refresh() } }
        RunLoop.main.run(until: Date().addingTimeInterval(0.2))
        wait { !warm.isRefreshing }
        results["runs_for_5_overlapping_requests"] = warm.reportRuns - runsBeforeBurst

        let data = try? JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
        print(String(decoding: data ?? Data(), as: UTF8.self))
        exit(0)
    }
}
