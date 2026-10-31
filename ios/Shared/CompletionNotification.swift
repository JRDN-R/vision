import Foundation
import UserNotifications

// Shared by the app and Share extension: a server-ready job is not yet a
// completed iPhone download. Success alerts require verified local files;
// failure alerts describe a durable retry state and never claim completion.
enum CompletionNotification {
    static func sendFailure(_ record: DownloadRecord) async {
        guard record.uid == NativeSession.currentUID, record.needsFailureNotification,
              let descriptor = try? TransferCompletion.lock(record.id) else { return }
        defer { TransferCompletion.unlock(descriptor) }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        // A retry or deletion may have happened while awaiting permission state.
        guard let current = try? DownloadStore.shared.record(id: record.id),
              current.uid == NativeSession.currentUID, current.needsFailureNotification,
              current.retryAttempt == record.retryAttempt, current.updatedAt == record.updatedAt else { return }
        guard [.authorized, .provisional].contains(settings.authorizationStatus) else { return }
        let content = UNMutableNotificationContent()
        content.title = "Vortex download failed"
        content.body = String((current.lastError ?? "Open Vision Downloads and tap Retry.").prefix(500))
        content.sound = .default
        content.userInfo = ["kind": "download", "recordID": record.id, "uid": record.uid]
        do {
            try await center.add(UNNotificationRequest(identifier: "vortex-failed-" + record.id, content: content, trigger: nil))
            try DownloadStore.shared.update(id: record.id) { latest in
                guard latest.needsFailureNotification, latest.retryAttempt == current.retryAttempt,
                      latest.updatedAt == current.updatedAt else { return }
                latest.failureNotificationAt = Date()
                latest.failureNotificationPending = false
            }
        } catch { /* Retry on the next foreground refresh. The error stays in Downloads. */ }
    }

    static func send(_ record: DownloadRecord) async {
        guard record.uid == NativeSession.currentUID, record.canExport else { return }
        guard let descriptor = try? TransferCompletion.lock(record.id) else { return }
        defer { TransferCompletion.unlock(descriptor) }
        guard let current = try? DownloadStore.shared.record(id: record.id),
              current.uid == NativeSession.currentUID, current.canExport, current.completionNotificationPending == true, current.completionNotificationAt == nil else { return }
        let record = current
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard [.authorized, .provisional].contains(settings.authorizationStatus), record.uid == NativeSession.currentUID else {
            try? DownloadStore.shared.update(id: record.id) { $0.notificationError = "Download alert not sent. Enable notifications for Vision in iPhone Settings." }
            return
        }
        let content = UNMutableNotificationContent()
        content.title = "Vortex download complete"
        content.body = record.savedAssetIdentifier != nil ? "Your video was saved to Photos. Tap to open Photos." : record.mode == .video ? "Your video is downloaded, but has not saved to Photos. Open Vision to finish saving." : "Tap to choose where to save your file in Files."
        content.sound = .default
        content.userInfo = ["kind": "download", "recordID": record.id, "uid": record.uid]
        do {
            try await center.add(UNNotificationRequest(identifier: "vortex-" + record.id, content: content, trigger: nil))
            try DownloadStore.shared.update(id: record.id) { current in
                current.completionNotificationAt = Date()
                current.completionNotificationPending = false
                current.notificationError = nil
            }
        } catch {
            try? DownloadStore.shared.update(id: record.id) { $0.notificationError = "Download alert could not be scheduled. Vision will retry when opened." }
        }
    }
}
