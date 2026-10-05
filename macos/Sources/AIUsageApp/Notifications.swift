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
        let defaults = UserDefaults.standard
        var delivered = Set(defaults.stringArray(forKey: deliveredKey) ?? [])
        let alerts = NotificationPlanner.alerts(for: report, now: now, preferences: preferences, delivered: delivered)
        guard !alerts.isEmpty else { return }
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return }
            for alert in alerts {
                let content = UNMutableNotificationContent()
                content.title = alert.title
                content.body = alert.body
                center.add(UNNotificationRequest(identifier: alert.key, content: content, trigger: nil))
            }
        }
        delivered.formUnion(alerts.map(\.key))
        defaults.set(Array(delivered.suffix(500)), forKey: deliveredKey)
    }
}
