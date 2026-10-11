import XCTest
@testable import AIUsageCore

// MARK: - Isolated notification doubles (never touch the system notification center)

struct SubmitFailure: Error {}

/// A notification center whose authorization prompt and submissions complete only
/// when the test says so.
@MainActor
final class FakeNotificationCenter: NotificationCenterClient {
    var status: NotificationAuthorization = .notDetermined
    private(set) var authorizationRequests = 0
    /// Every submission attempt, in order.
    private(set) var submitted: [String] = []
    var holdAuthorization = false
    var holdSubmissions = false
    var failNextSubmission = false
    private var authorizationWaiters: [CheckedContinuation<Bool, Never>] = []
    private var submissionWaiters: [CheckedContinuation<Bool, Never>] = []

    var pendingAuthorizations: Int { authorizationWaiters.count }
    var pendingSubmissions: Int { submissionWaiters.count }

    func authorization() async -> NotificationAuthorization { status }

    func requestAuthorization() async -> Bool {
        authorizationRequests += 1
        if holdAuthorization { return await withCheckedContinuation { authorizationWaiters.append($0) } }
        return status == .authorized
    }

    func answerPrompt(grant: Bool) {
        status = grant ? .authorized : .denied
        let waiters = authorizationWaiters
        authorizationWaiters = []
        waiters.forEach { $0.resume(returning: grant) }
    }

    func add(_ alert: PlannedAlert) async throws {
        submitted.append(alert.key)
        if holdSubmissions {
            let ok = await withCheckedContinuation { submissionWaiters.append($0) }
            if !ok { throw SubmitFailure() }
        } else if failNextSubmission {
            failNextSubmission = false
            throw SubmitFailure()
        }
    }

    func completeSubmissions(success: Bool = true) {
        let waiters = submissionWaiters
        submissionWaiters = []
        waiters.forEach { $0.resume(returning: success) }
    }
}

@MainActor
final class MemoryDeliveredStore: DeliveredAlertStore {
    private(set) var writes: [String] = []
    var deliveredKeys: Set<String> { Set(writes) }
    func markDelivered(_ key: String) { writes.append(key) }
}

@MainActor
private func settle(until condition: () -> Bool) async {
    for _ in 0..<2_000 where !condition() { await Task.yield() }
    for _ in 0..<50 { await Task.yield() }  // let any other ready task reach its next suspension
}

// MARK: - Tests

@MainActor
final class NotificationSchedulerTests: XCTestCase {
    private let limit = PlannedAlert(key: "limit|codex|weekly|2026-10-07", title: "Codex weekly limit reached", body: "Resets Tue 21:28.")

    func testOverlappingReportsWhileAuthorizationIsPendingSubmitOnce() async {
        let center = FakeNotificationCenter()
        let store = MemoryDeliveredStore()
        let scheduler = NotificationScheduler(center: center, store: store)
        center.holdAuthorization = true
        // Two reports carry the same alert while the permission prompt is open.
        let first = Task { await scheduler.schedule([limit]) }
        let second = Task { await scheduler.schedule([limit]) }
        await settle(until: { center.pendingAuthorizations > 0 })
        center.answerPrompt(grant: true)
        await first.value
        await second.value
        XCTAssertEqual(center.authorizationRequests, 1, "authorization work is coalesced")
        XCTAssertEqual(center.submitted, [limit.key], "exactly one request is submitted")
        XCTAssertEqual(store.writes, [limit.key], "delivery is persisted once")
    }

    func testDifferentAlertsWhileAuthorizationIsPendingShareOnePrompt() async {
        let center = FakeNotificationCenter()
        let store = MemoryDeliveredStore()
        let scheduler = NotificationScheduler(center: center, store: store)
        let failure = PlannedAlert(key: "failure|grok||2026-10-09T08:00:00Z", title: "Grok Build check failed", body: "")
        center.holdAuthorization = true
        let first = Task { await scheduler.schedule([limit]) }
        let second = Task { await scheduler.schedule([failure, limit]) }
        await settle(until: { center.pendingAuthorizations > 0 })
        center.answerPrompt(grant: true)
        await first.value
        await second.value
        XCTAssertEqual(center.authorizationRequests, 1, "one prompt serves every waiting report")
        XCTAssertEqual(center.submitted.sorted(), [failure.key, limit.key].sorted())
        XCTAssertEqual(store.writes.count, 2)
    }

    func testOverlappingReportsWhileSubmissionIsPendingSubmitOnce() async {
        let center = FakeNotificationCenter()
        center.status = .authorized
        center.holdSubmissions = true
        let store = MemoryDeliveredStore()
        let scheduler = NotificationScheduler(center: center, store: store)
        let first = Task { await scheduler.schedule([limit]) }
        await settle(until: { center.pendingSubmissions > 0 })
        XCTAssertTrue(store.writes.isEmpty, "nothing is persisted before the system accepts it")
        let second = Task { await scheduler.schedule([limit]) }
        await settle(until: { false })
        center.completeSubmissions()
        await settle(until: { center.pendingSubmissions > 0 })
        center.completeSubmissions()
        await first.value
        await second.value
        XCTAssertEqual(center.submitted, [limit.key], "an in-flight alert is not submitted again")
        XCTAssertEqual(store.writes, [limit.key])
        await scheduler.schedule([limit])
        XCTAssertEqual(center.submitted, [limit.key], "a delivered alert is not submitted again")
    }

    func testFailedSubmissionIsRetriedAndNotPersisted() async {
        let center = FakeNotificationCenter()
        center.status = .authorized
        center.failNextSubmission = true
        let store = MemoryDeliveredStore()
        let scheduler = NotificationScheduler(center: center, store: store)
        await scheduler.schedule([limit])
        XCTAssertEqual(center.submitted, [limit.key])
        XCTAssertTrue(store.writes.isEmpty, "a failed submission is not marked delivered")
        await scheduler.schedule([limit])
        XCTAssertEqual(center.submitted, [limit.key, limit.key], "the next report retries")
        XCTAssertEqual(store.writes, [limit.key])
    }

    func testFailureWhileAnotherReportWaitsReleasesTheReservation() async {
        let center = FakeNotificationCenter()
        center.status = .authorized
        center.holdSubmissions = true
        let store = MemoryDeliveredStore()
        let scheduler = NotificationScheduler(center: center, store: store)
        let first = Task { await scheduler.schedule([limit]) }
        await settle(until: { center.pendingSubmissions > 0 })
        center.completeSubmissions(success: false)
        await first.value
        XCTAssertTrue(store.writes.isEmpty)
        center.holdSubmissions = false
        await scheduler.schedule([limit])
        XCTAssertEqual(center.submitted, [limit.key, limit.key], "the reservation was cleared on failure")
        XCTAssertEqual(store.writes, [limit.key])
    }

    func testDenialClearsReservationsAndALaterGrantDelivers() async {
        let center = FakeNotificationCenter()
        center.holdAuthorization = true
        let store = MemoryDeliveredStore()
        let scheduler = NotificationScheduler(center: center, store: store)
        let first = Task { await scheduler.schedule([limit]) }
        let second = Task { await scheduler.schedule([limit]) }
        await settle(until: { center.pendingAuthorizations > 0 })
        center.answerPrompt(grant: false)
        await first.value
        await second.value
        XCTAssertTrue(center.submitted.isEmpty, "nothing is shown without permission")
        XCTAssertTrue(store.writes.isEmpty)
        center.status = .authorized  // granted later in System Settings
        center.holdAuthorization = false
        await scheduler.schedule([limit])
        XCTAssertEqual(center.submitted, [limit.key])
        XCTAssertEqual(store.writes, [limit.key])
    }

    func testDistinctAlertsInOneReportAreEachSubmittedOnce() async {
        let center = FakeNotificationCenter()
        center.status = .authorized
        let store = MemoryDeliveredStore()
        let scheduler = NotificationScheduler(center: center, store: store)
        let failure = PlannedAlert(key: "failure|claude||2026-10-09T08:00:00Z", title: "Claude Code check failed", body: "")
        await scheduler.schedule([limit, failure, limit])
        XCTAssertEqual(center.submitted, [limit.key, failure.key])
        XCTAssertEqual(store.deliveredKeys, [limit.key, failure.key])
    }
}
