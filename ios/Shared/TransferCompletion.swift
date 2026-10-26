import Foundation
import Photos
import Darwin

/// A transfer can finish while either the containing app or its extension owns
/// the URLSession delegate. This common path prevents a fast download from
/// losing Apple's temporary file when the share extension is still visible.
enum TransferCompletion {
    static func lock(_ recordID: String) throws -> Int32? {
        let file = try DownloadStore.shared.directory(for: recordID).appendingPathComponent("finalize.lock")
        let descriptor = open(file.path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw VortexClientError.unavailableStorage }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else { close(descriptor); return nil }
        return descriptor
    }
    static func unlock(_ descriptor: Int32) { flock(descriptor, LOCK_UN); close(descriptor) }

    /// Must run synchronously before URLSession's download delegate returns.
    static func preserve(_ location: URL, record: DownloadRecord) throws -> URL {
        let archive = try DownloadStore.shared.directory(for: record.id).appendingPathComponent(UUID().uuidString + ".vtx")
        try FileManager.default.moveItem(at: location, to: archive)
        do {
            try DownloadStore.shared.update(id: record.id) { current in
                current.pendingArchiveFilename = archive.lastPathComponent
                current.phase = "Verifying downloaded file"
            }
        } catch { try? FileManager.default.removeItem(at: archive); throw error }
        return archive
    }

    /// nil means another process owns finalization or this account signed out.
    /// Pending archives are durable and retried when Vision next launches.
    static func finish(recordID: String, archive: URL) async throws -> DownloadRecord? {
        guard let descriptor = try lock(recordID) else { return nil }
        defer { unlock(descriptor) }
        guard let record = try DownloadStore.shared.record(id: recordID), record.uid == NativeSession.currentUID,
              record.status != .cancelled, record.pendingArchiveFilename == archive.lastPathComponent else { return nil }
        let directory = try DownloadStore.shared.directory(for: recordID)
        do {
            if record.canExport, let local = try? DownloadStore.shared.localURL(for: record),
               FileManager.default.fileExists(atPath: local.path) {
                _ = try await saveVideoWhileLocked(record)
                let final = try DownloadStore.shared.update(id: recordID) { current in
                    current.sessionIdentifier = nil
                    current.pendingArchiveFilename = nil
                }
                try? FileManager.default.removeItem(at: archive)
                return final
            }
            let result = try await Task.detached(priority: .utility) { try TransferArchive.unpack(archive, into: directory) }.value
            guard NativeSession.currentUID == record.uid,
                  try DownloadStore.shared.record(id: recordID)?.status != .cancelled else {
                try? FileManager.default.removeItem(at: result.url)
                return nil
            }
            guard let updated = try DownloadStore.shared.update(id: recordID, { current in
                current.completionNotificationPending = true
                current.localFilename = result.filename
                if result.isAudio { current.mode = .audio }
                current.status = .ready
                current.phase = result.isVideo ? "Ready to save" : "Choose where to save in Files"
                current.progress = 1
                current.bytesReceived = result.size
                current.lastError = nil
            }) else { return nil }
            if result.isVideo { _ = try await saveVideoWhileLocked(updated) }
            let final = try DownloadStore.shared.update(id: recordID) { current in
                current.sessionIdentifier = nil
                current.pendingArchiveFilename = nil
            }
            try? FileManager.default.removeItem(at: archive)
            return final
        } catch {
            try? FileManager.default.removeItem(at: archive)
            try? DownloadStore.shared.update(id: recordID) { current in
                current.pendingArchiveFilename = nil
                current.sessionIdentifier = nil
                guard current.status != .cancelled else { return }
                current.status = .failed
                current.phase = "Open Vision to retry the transfer"
                current.lastError = error.localizedDescription
            }
            throw error
        }
    }

    static func saveVideo(_ record: DownloadRecord) async throws -> DownloadRecord? {
        guard let descriptor = try lock(record.id) else { return nil }
        defer { unlock(descriptor) }
        guard let current = try DownloadStore.shared.record(id: record.id) else { return nil }
        return try await saveVideoWhileLocked(current)
    }

    private static func saveVideoWhileLocked(_ record: DownloadRecord) async throws -> DownloadRecord {
        guard record.uid == NativeSession.currentUID, record.savedAssetIdentifier == nil,
              let url = try? DownloadStore.shared.localURL(for: record), ["mp4", "mov"].contains(url.pathExtension.lowercased()) else { return record }
        let status = PHPhotoLibrary.authorizationStatus(for: .addOnly)
        // An extension never asks for Photos permission. The main app requests
        // it in the foreground; completion may use an existing grant.
        guard status == .authorized || status == .limited else { return record }
        try DownloadStore.shared.update(id: record.id) { current in
            current.photoSaveAttemptAt = Date()
            current.photoSaveAttempts = (current.photoSaveAttempts ?? 0) + 1
        }
        do {
            var assetID: String?
            try await PHPhotoLibrary.shared().performChanges {
                assetID = PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: url)?.placeholderForCreatedAsset?.localIdentifier
            }
            guard let assetID else { throw VortexClientError.invalidArchive("Photos could not import this video; use Save to Files instead") }
            return try DownloadStore.shared.update(id: record.id) { current in
                if current.completionNotificationAt == nil { current.completionNotificationPending = true }
                current.savedAssetIdentifier = assetID
                current.status = .saved
                current.phase = "Saved to Photos"
                current.lastError = nil
            } ?? record
        } catch {
            return try DownloadStore.shared.update(id: record.id) { current in
                current.status = .ready
                current.phase = "Video downloaded · Photos save needs attention"
                current.lastError = "Photos could not save this file. " + error.localizedDescription
            } ?? record
        }
    }
}
