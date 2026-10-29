import XCTest
@testable import Vision

final class TransferFailureTests: XCTestCase {
    private let cancelled = NSError(domain: NSURLErrorDomain, code: NSURLErrorCancelled)

    private func transfer() -> DownloadRecord {
        var record = DownloadRecord(uid: "test", url: URL(string: "https://youtube.com/watch?v=test")!, selection: .video, videoFormat: "mp4")
        record.jobID = "job"
        record.sessionIdentifier = "session"
        record.status = .transferring
        record.progress = 0.5
        return record
    }

    private func job(status: String = "error", id: String = "job") throws -> VortexJob {
        let data = try JSONSerialization.data(withJSONObject: [
            "id": id, "status": status, "phase": "Unable to finish",
            "error": "The converted file exceeds the server download size limit."
        ])
        return try JSONDecoder().decode(VortexJob.self, from: data)
    }

    func testServerFailureBeforeCancellationKeepsOriginalDiagnostic() {
        var record = transfer()
        record.status = .failed
        record.phase = "Unable to finish"
        record.lastError = "This operation took too long."
        TransferFailure.apply(cancelled, sessionIdentifier: "session", to: &record)
        XCTAssertNil(record.sessionIdentifier)
        XCTAssertEqual(record.status, .failed)
        XCTAssertEqual(record.phase, "Unable to finish")
        XCTAssertEqual(record.lastError, "This operation took too long.")
    }

    func testCancellationBeforePollingUsesServerFailure() throws {
        var record = transfer()
        TransferFailure.apply(cancelled, sessionIdentifier: "session", job: try job(), to: &record)
        XCTAssertEqual(record.status, .failed)
        XCTAssertEqual(record.phase, "Unable to finish")
        XCTAssertEqual(record.lastError, "The converted file exceeds the server download size limit.")
        XCTAssertNil(record.progress)
        XCTAssertNil(record.sessionIdentifier)
        XCTAssertEqual(record.jobID, "job")
    }

    func testServerCancellationAndExpiryArePreserved() throws {
        for status in ["cancelled", "expired"] {
            var record = transfer()
            TransferFailure.apply(cancelled, sessionIdentifier: "session", job: try job(status: status), to: &record)
            XCTAssertEqual(record.status, status == "cancelled" ? .cancelled : .failed)
            XCTAssertEqual(record.phase, "Unable to finish")
        }
    }

    func testInterruptedTransferRemainsRetryableWhenServerIsActiveCompleteOrUnavailable() throws {
        for serverJob in [nil, try job(status: "processing"), try job(status: "complete")] {
            var record = transfer()
            TransferFailure.apply(cancelled, sessionIdentifier: "session", job: serverJob, to: &record)
            XCTAssertTrue(record.canRetry)
            XCTAssertEqual(record.jobID, "job", "Retry must reuse an existing server job")
            XCTAssertNil(record.sessionIdentifier)
            XCTAssertNil(record.progress)
            XCTAssertEqual(record.lastError, "The iPhone transfer was interrupted. Open Vision and tap Retry to reconnect to the server job.")
        }
    }

    func testLateCallbackCannotModifyReplacementSession() throws {
        var record = transfer()
        record.sessionIdentifier = "replacement"
        let expected = record
        TransferFailure.apply(cancelled, sessionIdentifier: "session", job: try job(), to: &record)
        XCTAssertEqual(record, expected)
    }

    func testCancellationAndCompletedFilesCannotBecomeFailures() throws {
        for status in [DownloadStatus.cancelled, .ready, .saved] {
            var record = transfer()
            record.status = status
            record.phase = "Existing phase"
            record.lastError = "Existing detail"
            record.localFilename = status == .cancelled ? nil : "video.mp4"
            TransferFailure.apply(cancelled, sessionIdentifier: "session", job: try job(), to: &record)
            XCTAssertEqual(record.status, status)
            XCTAssertEqual(record.phase, "Existing phase")
            XCTAssertEqual(record.lastError, "Existing detail")
            XCTAssertNil(record.sessionIdentifier)
        }
    }

    func testWrongJobCannotSupplyFailureReason() throws {
        var record = transfer()
        TransferFailure.apply(cancelled, sessionIdentifier: "session", job: try job(id: "different"), to: &record)
        XCTAssertTrue(record.lastError?.contains("iPhone transfer was interrupted") == true)
    }

    func testTimeoutAndOtherErrorsRemainActionable() {
        var record = transfer()
        TransferFailure.apply(NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut), sessionIdentifier: "session", to: &record)
        XCTAssertTrue(record.lastError?.contains("timed out") == true)
        record = transfer()
        let diskError = NSError(domain: NSCocoaErrorDomain, code: 640, userInfo: [NSLocalizedDescriptionKey: "No space left on iPhone"])
        TransferFailure.apply(diskError, sessionIdentifier: "session", to: &record)
        XCTAssertEqual(record.lastError, "No space left on iPhone")
    }
}
