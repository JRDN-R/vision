import Foundation
import UserNotifications

// Shared by the app and Share extension: a server-ready job is not yet a
// completed iPhone download. Only verified local files reach this path.
enum CompletionNotification {
    static func send(_ record: DownloadRecord) async {
        guard record.uid == NativeSession.currentUID, record.canExport else { return }
        guard let descriptor = try? TransferCompletion.lock(record.id) else { return }
        defer { TransferCompletion.unlock(descriptor) }
        guard let current = try? DownloadStore.shared.record(id: record.id),
              current.uid == NativeSession.currentUID, current.canExport, current.completionNotificationAt == nil else { return }
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
                current.notificationError = nil
            }
        } catch {
            try? DownloadStore.shared.update(id: record.id) { $0.notificationError = "Download alert could not be scheduled. Vision will retry when opened." }
        }
    }
}
