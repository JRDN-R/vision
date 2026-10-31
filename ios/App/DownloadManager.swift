import Foundation
import Combine
import UIKit
import Photos
import UserNotifications

@MainActor
final class DownloadManager: NSObject, ObservableObject {
    static let shared = DownloadManager()
    @Published private(set) var records: [DownloadRecord] = []
    @Published var errorMessage: String?
    private var sessions: [String: URLSession] = [:]
    private var completions: [String: () -> Void] = [:]
    private var finishedEvents = Set<String>()
    private var pendingWork: [String: Int] = [:]
    private var refreshTask: Task<Void, Never>?
    private var pumping = false
    private var refreshing = false
    private var progressWriteTimes: [String: Date] = [:]
    private var recoveringArchives = Set<String>()

    private override init() {
        super.init()
        loadPublished()
        // Session identifiers are durable, including sessions originally made
        // by the share extension. Recreate them to receive Apple's callbacks.
        for record in (try? DownloadStore.shared.records()) ?? [] {
            if let identifier = record.sessionIdentifier { attach(identifier) }
        }
        Task { await recoverPendingArchives() }
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                if UIApplication.shared.applicationState == .active { await self?.refresh() }
                try? await Task.sleep(nanoseconds: 8_000_000_000)
            }
        }
    }

    func enqueue(urls: [URL], mode: DownloadSelection = .automatic, videoFormat: String = "mp4") async {
        guard let uid = NativeSession.currentUID else { errorMessage = VortexClientError.signedOut.localizedDescription; return }
        var seen = Set<String>()
        let valid = urls.compactMap(SharedLinks.validated).filter { seen.insert($0.absoluteString).inserted }.prefix(SharedLinks.maximumCount)
        guard !valid.isEmpty else { errorMessage = VortexClientError.invalidURL.localizedDescription; return }
        do {
            let values = valid.map { DownloadRecord(uid: uid, url: $0, selection: mode, videoFormat: videoFormat) }
            try DownloadStore.shared.insert(values)
            loadPublished()
            if values.contains(where: { $0.mode == .video }), UIApplication.shared.applicationState == .active { await requestPhotoAccess() }
            await pump()
        } catch { errorMessage = error.localizedDescription }
    }

    /// A download queued from Vision's existing web Vortex screen is adopted
    /// by ID; never create a second server job for the same user action.
    func adopt(jobID: String) async {
        guard let uid = NativeSession.currentUID else { errorMessage = VortexClientError.signedOut.localizedDescription; return }
        do {
            if let existing = try DownloadStore.shared.records(uid: uid).first(where: { $0.jobID == jobID }) {
                if existing.status == .failed || existing.status == .cancelled { await retry(existing) }
                else { loadPublished(); await pump() }
                return
            }
            let job = try await VortexClient(uid: uid).job(jobID)
            guard let input = job.input, let url = URL(string: input), SharedLinks.validated(url) != nil else { throw VortexClientError.invalidURL }
            var record = DownloadRecord(uid: uid, url: url, selection: job.downloadMode == "audio" ? .audio : .video,
                                        videoFormat: job.videoFormat ?? "mp4")
            record.jobID = job.id
            record.status = .queued
            record.phase = job.phase
            record.progress = job.normalizedProgress
            if let title = job.title, !title.isEmpty { record.title = title }
            try DownloadStore.shared.insert([record])
            loadPublished()
            if record.mode == .video, UIApplication.shared.applicationState == .active { await requestPhotoAccess() }
            await pump()
        } catch { errorMessage = error.localizedDescription }
    }

    func refresh() async {
        guard !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        loadPublished()
        await reconcileDownloadState()
        guard let uid = NativeSession.currentUID else { return }
        do {
            await recoverPendingArchives()
            // Recover a crash between claiming a slot and starting its task.
            for record in try DownloadStore.shared.records(uid: uid) {
                if record.status == .starting, record.sessionIdentifier == nil, Date().timeIntervalSince(record.updatedAt) > 120 {
                    try DownloadStore.shared.update(id: record.id) { $0.status = .queued }
                }
                if let identifier = record.sessionIdentifier { attach(identifier) }
            }
            let client = VortexClient(uid: uid)
            let active = try DownloadStore.shared.records(uid: uid).filter { $0.status.isActive && $0.jobID != nil }
            for record in active {
                guard let jobID = record.jobID, NativeSession.currentUID == uid else { continue }
                do {
                    let job = try await client.job(jobID)
                    let updated = try DownloadStore.shared.update(id: record.id) { current in
                        guard current.status.isActive else { return }
                        if let title = job.title, !title.isEmpty { current.title = title }
                        if ["error", "cancelled", "expired"].contains(job.status) {
                            current.status = job.status == "cancelled" ? .cancelled : .failed
                            current.phase = job.phase
                            current.lastError = job.error ?? "This server file is unavailable. Retry to create a new download."
                            current.progress = nil
                        } else if job.status == "complete" {
                            current.phase = current.sessionIdentifier == nil ? "Ready for transfer to iPhone" : "Transferring to iPhone"
                            current.progress = nil
                            if current.sessionIdentifier != nil { current.status = .transferring }
                        } else {
                            current.phase = job.phase
                            current.progress = job.normalizedProgress
                        }
                    }
                    if let updated {
                        await LiveActivityManager.shared.update(updated, allowStart: UIApplication.shared.applicationState == .active)
                        if updated.status == .failed || updated.status == .cancelled,
                           let identifier = updated.sessionIdentifier { sessions[identifier]?.invalidateAndCancel() }
                    }
                } catch {
                    // A temporary status failure does not cancel an already
                    // accepted background transfer or mark it complete.
                    if (error as? VortexAPIError)?.statusCode == 401 { errorMessage = error.localizedDescription; break }
                }
            }
            for record in try DownloadStore.shared.records(uid: uid) where record.shouldRetryPhotoSave() {
                await saveToPhotos(record)
            }
            for record in try DownloadStore.shared.records(uid: uid) where record.canExport && record.completionNotificationPending == true && record.completionNotificationAt == nil {
                await CompletionNotification.send(record)
            }
            loadPublished()
            await pump()
        } catch { errorMessage = error.localizedDescription }
        await reconcileDownloadState()
    }

    func retry(_ record: DownloadRecord) async {
        guard record.uid == NativeSession.currentUID else { return }
        do {
            if record.localFilename != nil { await saveToPhotos(record); return }
            var newServerJob = false
            if let jobID = record.jobID {
                let job = try await VortexClient(uid: record.uid).job(jobID)
                newServerJob = ["error", "cancelled", "expired"].contains(job.status)
            }
            if let identifier = record.sessionIdentifier {
                sessions.removeValue(forKey: identifier)?.invalidateAndCancel()
            }
            await LiveActivityManager.shared.end(recordID: record.id)
            try DownloadStore.shared.update(id: record.id) { current in
                current.sessionIdentifier = nil
                if newServerJob { current.jobID = nil; current.retryAttempt += 1 }
                current.status = current.jobID == nil ? .pending : .queued
                current.phase = "Retrying download"
                current.lastError = nil
                current.progress = nil
                current.bytesReceived = 0
                current.failureNotificationAt = nil
                current.liveActivityJobID = nil
            }
            loadPublished()
            await pump()
        } catch { errorMessage = error.localizedDescription }
    }

    func cancel(_ record: DownloadRecord) async {
        guard record.uid == NativeSession.currentUID else { return }
        do {
            try DownloadStore.shared.update(id: record.id) { current in
                current.status = .cancelled
                current.phase = "Cancelled"
                current.progress = nil
                current.sessionIdentifier = nil
            }
            // Clear the local activity before contacting the server. A network
            // error must not leave a cancelled download on Dynamic Island.
            await LiveActivityManager.shared.end(recordID: record.id)
            if let identifier = record.sessionIdentifier { attach(identifier); sessions[identifier]?.invalidateAndCancel() }
            if let jobID = record.jobID { try await VortexClient(uid: record.uid).cancel(jobID) }
            if let updated = try DownloadStore.shared.record(id: record.id) { await LiveActivityManager.shared.update(updated) }
        } catch { errorMessage = error.localizedDescription }
        loadPublished()
        await pump()
    }

    func remove(_ record: DownloadRecord) async {
        guard record.uid == NativeSession.currentUID else { return }
        if record.status.isActive || record.sessionIdentifier != nil { await cancel(record) }
        await LiveActivityManager.shared.end(recordID: record.id)
        do { try DownloadStore.shared.remove(id: record.id); loadPublished() }
        catch { errorMessage = error.localizedDescription }
    }

    func exportURL(for record: DownloadRecord) throws -> URL {
        guard record.uid == NativeSession.currentUID, record.canExport else { throw VortexClientError.signedOut }
        return try DownloadStore.shared.localURL(for: record)
    }

    func markExported(_ record: DownloadRecord) async {
        guard record.uid == NativeSession.currentUID, record.localFilename != nil else { return }
        do {
            let updated = try DownloadStore.shared.update(id: record.id) { current in
                current.status = .saved
                current.phase = "Saved to Files"
                current.lastError = nil
            }
            if let updated { await LiveActivityManager.shared.update(updated) }
            loadPublished()
        } catch { errorMessage = error.localizedDescription }
    }

    func requestPhotoAccess() async {
        guard UIApplication.shared.applicationState == .active else { return }
        if PHPhotoLibrary.authorizationStatus(for: .addOnly) == .notDetermined {
            _ = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        }
    }

    func saveToPhotos(_ record: DownloadRecord) async {
        do {
            if let updated = try await TransferCompletion.saveVideo(record) {
                await LiveActivityManager.shared.update(updated)
                await CompletionNotification.send(updated)
            }
        } catch { errorMessage = error.localizedDescription }
        loadPublished()
    }

    func signOut(uid: String) async {
        for record in (try? DownloadStore.shared.records(uid: uid)) ?? [] where record.status.isActive || record.sessionIdentifier != nil {
            try? DownloadStore.shared.update(id: record.id) { current in
                current.status = .cancelled
                current.phase = "Phone transfer stopped after sign-out"
                current.lastError = nil
                current.sessionIdentifier = nil
            }
            if let identifier = record.sessionIdentifier { attach(identifier); sessions[identifier]?.invalidateAndCancel() }
        }
        await LiveActivityManager.shared.endAll(uid: uid)
        records = []
    }

    func handleEvents(identifier: String, completion: @escaping () -> Void) {
        guard (try? DownloadStore.shared.record(sessionIdentifier: identifier)) != nil
                || pendingWork[identifier, default: 0] > 0 || sessions[identifier] != nil else { completion(); return }
        completions[identifier] = completion
        attach(identifier)
        finishIfReady(identifier)
    }

    private func attach(_ identifier: String) {
        guard sessions[identifier] == nil else { return }
        let session = URLSession(configuration: TransferScheduler.configuration(identifier: identifier), delegate: self, delegateQueue: .main)
        sessions[identifier] = session
        // If a process died before resuming its persisted task, resume it. A
        // session with no tasks is retried on the next foreground visit.
        session.getAllTasks { tasks in
            DispatchQueue.main.async {
                let record = try? DownloadStore.shared.record(sessionIdentifier: identifier)
                if record?.uid != NativeSession.currentUID || record?.status.isTerminal == true {
                    tasks.forEach { $0.cancel() }
                    if tasks.isEmpty, let record, record.status.isTerminal {
                        try? DownloadStore.shared.update(id: record.id) { $0.sessionIdentifier = nil }
                    }
                } else {
                    tasks.filter { $0.state == .suspended }.forEach { $0.resume() }
                    if tasks.isEmpty, self.pendingWork[identifier, default: 0] == 0,
                       let record, record.isMissingTransferOverdue() {
                        try? DownloadStore.shared.update(id: record.id) { current in
                            current.sessionIdentifier = nil
                            current.status = .failed
                            current.phase = "Open Vision to retry the transfer"
                            current.lastError = "iOS no longer has this transfer. The server job is preserved."
                        }
                        self.loadPublished()
                        Task { await self.reconcileDownloadState() }
                    }
                }
                if tasks.isEmpty, self.pendingWork[identifier, default: 0] == 0 {
                    // Recheck on a later refresh, including sessions observed
                    // empty before the crash-recovery grace period elapsed.
                    self.sessions.removeValue(forKey: identifier)?.finishTasksAndInvalidate()
                }
            }
        }
    }

    private func pump() async {
        guard !pumping, let uid = NativeSession.currentUID else { return }
        pumping = true
        defer { pumping = false; loadPublished() }
        do {
            if UIApplication.shared.applicationState == .active { try await TransferScheduler.submitPending(uid: uid) }
            if let session = try await TransferScheduler.scheduleNext(uid: uid, delegate: self), let identifier = session.configuration.identifier {
                sessions[identifier] = session
            }
            for record in try DownloadStore.shared.records(uid: uid) where record.status.isActive && record.jobID != nil {
                await LiveActivityManager.shared.update(record, allowStart: UIApplication.shared.applicationState == .active)
            }
        } catch { errorMessage = error.localizedDescription }
        await reconcileDownloadState()
    }

    private func reconcileDownloadState() async {
        await LiveActivityManager.shared.reconcile()
        guard let uid = NativeSession.currentUID else { return }
        for record in (try? DownloadStore.shared.records(uid: uid)) ?? [] where record.needsFailureNotification {
            await CompletionNotification.sendFailure(record)
        }
    }

    private func loadPublished() {
        do {
            records = try NativeSession.currentUID.map { try DownloadStore.shared.records(uid: $0) } ?? []
        } catch { errorMessage = error.localizedDescription }
    }

    private func finishIfReady(_ identifier: String) {
        guard finishedEvents.contains(identifier), pendingWork[identifier, default: 0] == 0 else { return }
        finishedEvents.remove(identifier)
        pendingWork.removeValue(forKey: identifier)
        progressWriteTimes.removeValue(forKey: identifier)
        sessions.removeValue(forKey: identifier)?.finishTasksAndInvalidate()
        completions.removeValue(forKey: identifier)?()
    }

    private func receivedFile(sessionIdentifier: String, recordID: String, archive: URL, httpStatus: Int) async {
        defer {
            pendingWork[sessionIdentifier, default: 0] -= 1
            finishIfReady(sessionIdentifier)
        }
        do {
            guard (200..<300).contains(httpStatus) else {
                try? FileManager.default.removeItem(at: archive)
                throw VortexAPIError(message: httpStatus == 503 ? "The server's transfer slots are busy. Open Vision and retry shortly." : "The transfer returned HTTP \(httpStatus). Open Vision to retry.", statusCode: httpStatus)
            }
            if let final = try await TransferCompletion.finish(recordID: recordID, archive: archive) {
                await LiveActivityManager.shared.update(final)
                await notifyReady(final)
            }
        } catch {
            try? DownloadStore.shared.update(id: recordID) { current in
                guard current.status != .cancelled, current.sessionIdentifier == sessionIdentifier else { return }
                current.status = .failed
                current.phase = "Open Vision to retry the transfer"
                current.lastError = error.localizedDescription
                current.sessionIdentifier = nil
                current.pendingArchiveFilename = nil
            }
        }
        loadPublished()
        await pump()
    }

    private func recoverPendingArchives() async {
        guard let uid = NativeSession.currentUID else { return }
        for record in (try? DownloadStore.shared.records(uid: uid)) ?? [] {
            guard let name = record.pendingArchiveFilename, !recoveringArchives.contains(record.id),
                  record.status != .cancelled, name == (name as NSString).lastPathComponent,
                  name.hasSuffix(".vtx"), UUID(uuidString: String(name.dropLast(4))) != nil,
                  let directory = try? DownloadStore.shared.directory(for: record.id) else { continue }
            let archive = directory.appendingPathComponent(name)
            guard FileManager.default.fileExists(atPath: archive.path) else { continue }
            recoveringArchives.insert(record.id)
            let identifier = record.sessionIdentifier ?? "recovery." + record.id
            pendingWork[identifier, default: 0] += 1
            await receivedFile(sessionIdentifier: identifier, recordID: record.id, archive: archive, httpStatus: 200)
            recoveringArchives.remove(record.id)
        }
    }

    private func notifyReady(_ record: DownloadRecord) async {
        await CompletionNotification.send(record)
    }

}

extension DownloadManager: URLSessionDownloadDelegate {
    nonisolated func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        guard let identifier = session.configuration.identifier,
              let record = try? DownloadStore.shared.record(sessionIdentifier: identifier) else { return }
        do {
            // Apple's temporary location expires when this delegate returns.
            let archive = try TransferCompletion.preserve(location, record: record)
            let status = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 0
            DispatchQueue.main.async {
                // Increment before scheduling the async parser. Otherwise the
                // next delegate event could release Apple's background window
                // while file validation is still waiting to start.
                self.pendingWork[identifier, default: 0] += 1
                Task { @MainActor in await self.receivedFile(sessionIdentifier: identifier, recordID: record.id, archive: archive, httpStatus: status) }
            }
        } catch {
            try? DownloadStore.shared.update(id: record.id) { current in
                current.status = .failed
                current.phase = "Could not store the downloaded file"
                current.lastError = error.localizedDescription
                current.sessionIdentifier = nil
            }
            DispatchQueue.main.async {
                self.loadPublished()
                Task { await self.reconcileDownloadState() }
            }
        }
    }

    nonisolated func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                               totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        guard let identifier = session.configuration.identifier else { return }
        DispatchQueue.main.async {
            let now = Date()
            guard now.timeIntervalSince(self.progressWriteTimes[identifier] ?? .distantPast) >= 1 else { return }
            self.progressWriteTimes[identifier] = now
            guard let record = try? DownloadStore.shared.record(sessionIdentifier: identifier), record.uid == NativeSession.currentUID else { return }
            let updated = try? DownloadStore.shared.update(id: record.id) { current in
                // The framed stream's final size is unknown during conversion.
                // Never turn heartbeat bytes into a fabricated completion %.
                current.applyTransferProgress(received: totalBytesWritten, expected: totalBytesExpectedToWrite)
            }
            self.loadPublished()
            if let updated, totalBytesExpectedToWrite > 0 { Task { await LiveActivityManager.shared.update(updated) } }
        }
    }

    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let error, let identifier = session.configuration.identifier else { return }
        DispatchQueue.main.async {
            self.pendingWork[identifier, default: 0] += 1
            Task { @MainActor in
                defer {
                    self.pendingWork[identifier, default: 0] -= 1
                    self.finishIfReady(identifier)
                }
                if let updated = await TransferFailure.reconcile(error, sessionIdentifier: identifier) {
                    await LiveActivityManager.shared.update(updated)
                }
                self.loadPublished()
                await self.pump()
            }
        }
    }

    nonisolated func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        guard let identifier = session.configuration.identifier else { return }
        DispatchQueue.main.async {
            self.finishedEvents.insert(identifier)
            self.finishIfReady(identifier)
        }
    }

    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                               newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // Transfer capabilities are never forwarded to a redirected URL.
        completionHandler(nil)
    }
}
