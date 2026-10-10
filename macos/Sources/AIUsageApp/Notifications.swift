import AIUsageCore
import Foundation
import UserNotifications

/// Delivers NotificationPlanner alerts through the system, once each.
@MainActor
enum NotificationCenterBridge {
    static let limitsKey = "notifyLimits"
    static let failuresKey = "notifyFailures"
    private static let deliveredKey = "deliveredNotificationKeys"

    static var preferences: NotificationPreferences {
        let defaults = UserDefaults.standard
        return NotificationPreferences(
            limits: defaults.object(forKey: limitsKey) as? Bool ?? true,
            failures: defaults.object(forKey: failuresKey) as? Bool ?? true)
    }

    static func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    static func handle(_ report: Report, now: Date) {
        let delivered = Set(UserDefaults.standard.stringArray(forKey: deliveredKey) ?? [])
        let alerts = NotificationPlanner.alerts(for: report, now: now, preferences: preferences, delivered: delivered)
        guard !alerts.isEmpty else { return }
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            switch settings.authorizationStatus {
            case .authorized, .provisional:
                deliver(alerts)
            case .notDetermined:
                // Both switches default to on, so permission is asked for the first
                // time there is something to say. Without it nothing is shown or
                // marked delivered: current conditions still notify once granted.
                UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
                    if granted { deliver(alerts) }
                }
            default:
                return
            }
        }
    }

    nonisolated private static func deliver(_ alerts: [PlannedAlert]) {
        let center = UNUserNotificationCenter.current()
        for alert in alerts {
            let content = UNMutableNotificationContent()
            content.title = alert.title
            content.body = alert.body
            center.add(UNNotificationRequest(identifier: alert.key, content: content, trigger: nil)) { error in
                guard error == nil else { return }
                Task { @MainActor in markDelivered(alert.key) }
            }
        }
    }

    private static func markDelivered(_ key: String) {
        var keys = UserDefaults.standard.stringArray(forKey: deliveredKey) ?? []
        guard !keys.contains(key) else { return }
        keys.append(key)
        UserDefaults.standard.set(Array(keys.suffix(500)), forKey: deliveredKey)
    }
}
