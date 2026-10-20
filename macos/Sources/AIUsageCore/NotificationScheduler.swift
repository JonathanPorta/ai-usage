import Foundation

/// The permission state that matters for delivery.
public enum NotificationAuthorization: Sendable, Equatable {
    case authorized, notDetermined, denied
}

/// The system notification center, behind a seam so delivery can be tested with doubles.
@MainActor
public protocol NotificationCenterClient: AnyObject {
    func authorization() async -> NotificationAuthorization
    /// Shows the system prompt; returns whether permission was granted.
    func requestAuthorization() async -> Bool
    /// Submits one alert. Returning means the system accepted it.
    func add(_ alert: PlannedAlert) async throws
}

/// Keys of alerts the system has accepted, kept across launches.
@MainActor
public protocol DeliveredAlertStore: AnyObject {
    var deliveredKeys: Set<String> { get }
    func markDelivered(_ key: String)
}

/// Delivers planned alerts through the system, once each.
///
/// Reports arrive independently, and both the permission prompt and each
/// submission complete asynchronously. On the main actor, every alert key is
/// reserved before the first suspension, so an overlapping report cannot submit
/// it again; one authorization request serves every waiting report; delivered
/// keys are rechecked just before submission; and a key is persisted only once
/// the system accepts it. Denial or a failed submission releases the reservation,
/// so a later report retries.
@MainActor
public final class NotificationScheduler {
    private let center: NotificationCenterClient
    private let store: DeliveredAlertStore
    /// Keys reserved by a report that has not finished submitting them.
    public private(set) var inFlight: Set<String> = []
    private var authorizing: Task<Bool, Never>?

    public init(center: NotificationCenterClient, store: DeliveredAlertStore) {
        self.center = center
        self.store = store
    }

    public func schedule(_ alerts: [PlannedAlert]) async {
        var claimed: [PlannedAlert] = []
        for alert in alerts where !store.deliveredKeys.contains(alert.key) && !inFlight.contains(alert.key) {
            inFlight.insert(alert.key)
            claimed.append(alert)
        }
        guard !claimed.isEmpty else { return }
        guard await isAuthorized() else {
            claimed.forEach { inFlight.remove($0.key) }
            return
        }
        for alert in claimed {
            defer { inFlight.remove(alert.key) }
            guard !store.deliveredKeys.contains(alert.key) else { continue }
            do {
                try await center.add(alert)
                store.markDelivered(alert.key)
            } catch {
                // Not persisted: the next report that still warrants it retries.
            }
        }
    }

    private func isAuthorized() async -> Bool {
        if let authorizing { return await authorizing.value }
        switch await center.authorization() {
        case .authorized: return true
        case .denied: return false
        case .notDetermined:
            // Another report may have started the prompt while we checked.
            if let authorizing { return await authorizing.value }
            let center = self.center
            let request = Task { await center.requestAuthorization() }
            authorizing = request
            let granted = await request.value
            authorizing = nil
            return granted
        }
    }
}
