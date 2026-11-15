import Foundation

/// Plain-words text for the Settings tracking control.
public enum SettingsTracking {
    public static func shortLabel(_ mode: TrackingMode) -> String {
        mode == .auto ? "Auto" : mode.label
    }

    /// What detection found; nil when there is nothing truthful to say
    /// (no detection data, or a plugin with no install requirement).
    public static func detectionText(_ provider: Provider) -> String? {
        guard let detection = provider.detection, detection.requirement != Detection.Requirement.none else { return nil }
        switch detection.installed {
        case true?: return "Installed"
        case false?: return "Not found"
        // The setup line already says "Not found…"; don't contradict it.
        case nil: return provider.setup == .notDetected ? nil : "Detection unknown"
        }
    }

    /// "Saving…" while pending; under Auto, what Auto is doing now.
    public static func statusText(_ provider: Provider, shown: TrackingMode, saving: Bool) -> String? {
        if saving { return "Saving…" }
        guard shown == .auto else { return nil }
        switch provider.effectiveTracking {
        case .active: return "Auto · tracking"
        case .inactive: return "Auto · not tracking"
        case .pending, .unknown: return "Auto · checking"
        }
    }
}
