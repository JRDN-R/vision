import XCTest
@testable import Vision

final class DownloadLifecycleTests: XCTestCase {
    private func record(_ status: DownloadStatus = .processing) -> DownloadRecord {
        var result = DownloadRecord(uid: "test", url: URL(string: "https://example.com/video")!, selection: .video, videoFormat: "mp4")
        result.jobID = "job"
        result.status = status
        return result
    }

    func testRelaunchCleansDeletedFailedFinishedAndOtherAccountActivities() {
        for status in [DownloadStatus.failed, .cancelled, .ready, .saved] {
            let r = record(status)
            let entry = LiveActivityReconciliation.Entry(id: "activity", recordID: r.id, jobID: "job", isActive: true)
            XCTAssertEqual(LiveActivityReconciliation.obsoleteIDs([entry], records: [r], uid: "test"), ["activity"])
        }
        let r = record()
        let entry = LiveActivityReconciliation.Entry(id: "activity", recordID: r.id, jobID: "job", isActive: true)
        XCTAssertEqual(LiveActivityReconciliation.obsoleteIDs([entry], records: [], uid: "test"), ["activity"])
        XCTAssertEqual(LiveActivityReconciliation.obsoleteIDs([entry], records: [r], uid: nil), ["activity"])
        XCTAssertEqual(LiveActivityReconciliation.obsoleteIDs([entry], records: [r], uid: "other"), ["activity"])
        XCTAssertTrue(LiveActivityReconciliation.obsoleteIDs([entry], records: [r], uid: "test").isEmpty)
    }

    func testRetryKeepsOneCurrentActivityAndRemovesPreviousJobAndDuplicates() {
        let r = record()
        let entries: [LiveActivityReconciliation.Entry] = [
            .init(id: "old", recordID: r.id, jobID: "old-job", isActive: true),
            .init(id: "ended", recordID: r.id, jobID: "job", isActive: false),
            .init(id: "current", recordID: r.id, jobID: "job", isActive: true),
            .init(id: "duplicate", recordID: r.id, jobID: "job", isActive: true)
        ]
        XCTAssertEqual(LiveActivityReconciliation.obsoleteIDs(entries, records: [r], uid: "test"), ["old", "ended", "duplicate"])
    }

    func testLateProgressCannotResurrectTerminalDownload() {
        for status in [DownloadStatus.failed, .cancelled, .ready, .saved] {
            var r = record(status)
            r.lastError = "Original failure"
            let original = r
            r.applyTransferProgress(received: 50, expected: 100)
            XCTAssertEqual(r, original)
        }
        var active = record()
        active.applyTransferProgress(received: 50, expected: 100)
        XCTAssertEqual(active.status, .transferring)
        XCTAssertEqual(active.progress, 0.5)
    }

    func testDismissedActivityDoesNotRestartOnPollOrRelaunch() throws {
        var r = record()
        XCTAssertTrue(r.canStartLiveActivity)
        r.liveActivityJobID = r.jobID
        let restored = try JSONDecoder().decode(DownloadRecord.self, from: JSONEncoder().encode(r))
        XCTAssertFalse(restored.canStartLiveActivity)
        r.liveActivityJobID = nil // Explicit Retry may start it again.
        XCTAssertTrue(r.canStartLiveActivity)
        r.status = .failed
        XCTAssertFalse(r.canStartLiveActivity)
    }

    func testStatusPollingCannotKeepAMissingTransferAliveForever() {
        var r = record()
        let start = Date()
        r.sessionIdentifier = "session"
        r.transferStartedAt = start
        XCTAssertFalse(r.isMissingTransferOverdue(now: start.addingTimeInterval(59)))
        r.updatedAt = start.addingTimeInterval(120) // A fresh server poll is not a phone task.
        XCTAssertTrue(r.isMissingTransferOverdue(now: start.addingTimeInterval(120)))
        r.pendingArchiveFilename = "pending.vtx"
        XCTAssertFalse(r.isMissingTransferOverdue(now: start.addingTimeInterval(120)))
        r.pendingArchiveFilename = nil
        r.status = .saved
        XCTAssertFalse(r.isMissingTransferOverdue(now: start.addingTimeInterval(120)))
    }

    func testFailureAlertsAreOncePerAttemptAndDoNotReplayHistoricalErrors() throws {
        var r = record(.failed)
        let restored = try JSONDecoder().decode(DownloadRecord.self, from: JSONEncoder().encode(r))
        XCTAssertFalse(restored.needsFailureNotification)
        r.trackStatusChange(from: .processing)
        XCTAssertTrue(r.needsFailureNotification)
        r.failureNotificationAt = Date()
        r.failureNotificationPending = false
        XCTAssertFalse(r.needsFailureNotification)
        r.status = .queued
        r.trackStatusChange(from: .failed)
        XCTAssertFalse(r.needsFailureNotification)
        r.status = .failed
        r.trackStatusChange(from: .queued)
        XCTAssertTrue(r.needsFailureNotification)
        r.status = .cancelled
        r.trackStatusChange(from: .failed)
        XCTAssertFalse(r.needsFailureNotification)
    }
}
