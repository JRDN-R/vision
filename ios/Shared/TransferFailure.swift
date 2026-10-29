import Foundation

/// Both URLSession delegates use the same failure policy. A server failure can
/// arrive before or after the transport callback caused by cancelling its task.
enum TransferFailure {
    static func apply(_ error: Error, sessionIdentifier: String, job: VortexJob? = nil,
                      to record: inout DownloadRecord) {
        // A late callback must never affect a retry's replacement session.
        guard record.sessionIdentifier == sessionIdentifier else { return }
        record.sessionIdentifier = nil
        // Preserve authoritative server failures, intentional cancellation and
        // completed local files, including their phase and diagnostic message.
        guard record.status.isActive else { return }
        record.progress = nil
        if let job, job.id == record.jobID, ["error", "cancelled", "expired"].contains(job.status) {
            record.status = job.status == "cancelled" ? .cancelled : .failed
            record.phase = job.phase
            record.lastError = job.error ?? "This server file is unavailable. Retry to create a new download."
            return
        }
        record.status = .failed
        record.phase = "Open Vision to retry the transfer"
        let failure = error as NSError
        if failure.domain == NSURLErrorDomain && failure.code == NSURLErrorCancelled {
            record.lastError = "The iPhone transfer was interrupted. Open Vision and tap Retry to reconnect to the server job."
        } else if failure.domain == NSURLErrorDomain && failure.code == NSURLErrorTimedOut {
            record.lastError = "The iPhone transfer timed out. Open Vision and tap Retry to reconnect to the server job."
        } else {
            record.lastError = error.localizedDescription
        }
    }

    static func reconcile(_ error: Error, sessionIdentifier: String) async -> DownloadRecord? {
        guard let record = try? DownloadStore.shared.record(sessionIdentifier: sessionIdentifier),
              record.uid == NativeSession.currentUID else { return nil }
        var job: VortexJob?
        if record.status.isActive, let jobID = record.jobID {
            // If cancellation won the race with polling, recover the actual
            // server reason before storing a generic transport failure.
            job = try? await VortexClient(uid: record.uid).job(jobID)
        }
        guard record.uid == NativeSession.currentUID else { return nil }
        return try? DownloadStore.shared.update(id: record.id) { current in
            apply(error, sessionIdentifier: sessionIdentifier, job: job, to: &current)
        }
    }
}
