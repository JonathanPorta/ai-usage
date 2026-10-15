/// SF Symbol names, from the proposed mapping in handoff IMPLEMENTATION.md §12.
/// Symbols are referenced by name only; no icon artwork is bundled.
public enum Symbols {
    public static let appMark = "waveform.path.ecg"
    public static let healthy = "checkmark.circle"
    public static let attention = "exclamationmark.circle"
    public static let failure = "xmark.circle"
    public static let paused = "pause.circle"
    public static let stopped = "stop.circle"
    public static let clock = "clock"
    public static let stale = "clock.badge.exclamationmark"
    public static let limit = "gauge.with.dots.needle.100percent"
    public static let signIn = "lock"
    public static let notSetUp = "powerplug"
    public static let waiting = "hourglass"
    public static let collect = "arrow.clockwise"
    public static let history = "chart.bar"
    public static let settings = "gearshape"
    public static let log = "doc.text"
    public static let info = "info.circle"
    public static let back = "chevron.left"
    public static let forward = "chevron.right"
    public static let unavailable = "minus"

    public enum Badge {
        public static let collecting = "arrow.triangle.2.circlepath"
        public static let paused = "pause.circle.fill"
        public static let stopped = "stop.circle.fill"
        public static let attention = "exclamationmark.circle.fill"
    }
}
