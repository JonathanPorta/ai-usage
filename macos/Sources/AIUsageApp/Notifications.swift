import AIUsageCore
import Foundation
import UserNotifications

/// Delivers NotificationPlanner alerts through the system, once each. Scheduling,
/// permission coalescing and de-duplication live in `NotificationScheduler`.
@MainActor
enum NotificationCenterBridge {
    static let limitsKey = "notifyLimits"
    static let failuresKey = "notifyFailures"

    static var preferences: NotificationPreferences {
        let defaults = UserDefaults.standard
        return NotificationPreferences(
            limits: defaults.object(forKey: limitsKey) as? Bool ?? true,
            failures: defaults.object(forKey: failuresKey) as? Bool ?? true)
    }

    private static let store = UserDefaultsDeliveredStore()
    private static let scheduler = NotificationScheduler(center: SystemNotificationCenter(), store: store)

    static func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    static func handle(_ report: Report, now: Date) {
        let alerts = NotificationPlanner.alerts(for: report, now: now, preferences: preferences, delivered: store.deliveredKeys)
        guard !alerts.isEmpty else { return }
        // Both switches default to on, so permission is asked for the first time
        // there is something to say (once, however many reports are waiting).
        Task { await scheduler.schedule(alerts) }
    }
}

/// The real notification center.
@MainActor
final class SystemNotificationCenter: NotificationCenterClient {
    func authorization() async -> NotificationAuthorization {
        switch await UNUserNotificationCenter.current().notificationSettings().authorizationStatus {
        case .authorized, .provisional, .ephemeral: return .authorized
        case .notDetermined: return .notDetermined
        default: return .denied
        }
    }

    func requestAuthorization() async -> Bool {
        (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])) ?? false
    }

    func add(_ alert: PlannedAlert) async throws {
        let content = UNMutableNotificationContent()
        content.title = alert.title
        content.body = alert.body
        try await UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: alert.key, content: content, trigger: nil))
    }
}

/// Delivered keys in the app's preferences (the most recent 500).
@MainActor
final class UserDefaultsDeliveredStore: DeliveredAlertStore {
    private let key = "deliveredNotificationKeys"

    var deliveredKeys: Set<String> { Set(UserDefaults.standard.stringArray(forKey: key) ?? []) }

    func markDelivered(_ alertKey: String) {
        var keys = UserDefaults.standard.stringArray(forKey: key) ?? []
        guard !keys.contains(alertKey) else { return }
        keys.append(alertKey)
        UserDefaults.standard.set(Array(keys.suffix(500)), forKey: key)
    }
}
