import XCTest
@testable import Vision

final class DownloadCompletionTests: XCTestCase {
    private func video() -> DownloadRecord {
        var record = DownloadRecord(uid: "test", url: URL(string: "https://youtube.com/watch?v=test")!, selection: .video, videoFormat: "mp4")
        record.localFilename = "video.mp4"
        record.status = .ready
        return record
    }

    func testPhotoFailureRemainsEligibleForBoundedRetry() {
        var record = video()
        record.lastError = "Photos temporarily unavailable"
        XCTAssertTrue(record.shouldRetryPhotoSave())
        let now = Date()
        record.photoSaveAttemptAt = now
        record.photoSaveAttempts = 1
        XCTAssertFalse(record.shouldRetryPhotoSave(now: now.addingTimeInterval(59)))
        XCTAssertTrue(record.shouldRetryPhotoSave(now: now.addingTimeInterval(61)))
        record.photoSaveAttempts = 3
        XCTAssertFalse(record.shouldRetryPhotoSave(now: now.addingTimeInterval(3600)))
    }

    func testSavedCancelledAudioAndNonVideoFilesAreNotImported() {
        var record = video()
        record.savedAssetIdentifier = "photos-id"
        XCTAssertFalse(record.shouldRetryPhotoSave())
        record.savedAssetIdentifier = nil
        record.status = .cancelled
        XCTAssertFalse(record.shouldRetryPhotoSave())
        record.status = .ready
        record.mode = .audio
        XCTAssertFalse(record.shouldRetryPhotoSave())
        record.mode = .video
        record.localFilename = "gallery.zip"
        XCTAssertFalse(record.shouldRetryPhotoSave())
    }

    func testOldDownloadRecordsDecodeAndDeliveryStateSurvivesRestart() throws {
        let encoder = JSONEncoder(), decoder = JSONDecoder()
        var record = video()
        let old = try encoder.encode(record)
        let restored = try decoder.decode(DownloadRecord.self, from: old)
        XCTAssertNil(restored.completionNotificationAt)
        XCTAssertNil(restored.completionNotificationPending, "Do not replay alerts for historical downloads")
        XCTAssertTrue(restored.shouldRetryPhotoSave())
        record.completionNotificationPending = false
        record.completionNotificationAt = Date(timeIntervalSince1970: 1234)
        record.photoSaveAttempts = 2
        record.photoSaveAttemptAt = Date(timeIntervalSince1970: 1200)
        record.notificationError = "Retry notification"
        XCTAssertEqual(try decoder.decode(DownloadRecord.self, from: encoder.encode(record)), record)
    }
}
