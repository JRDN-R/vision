import Foundation
import UserNotifications

// Shared by the app and Share extension: a server-ready job is not yet a
// completed iPhone download. Only verified local files reach this path.
enum CompletionNotification {
    static func send(_ record: DownloadRecord) async {
        guard record.uid == NativeSession.currentUID, record.canExport else { return }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard [.authorized, .provisional].contains(settings.authorizationStatus), record.uid == NativeSession.currentUID else { return }
        let content = UNMutableNotificationContent()
        content.title = "Vortex download complete"
        content.body = record.savedAssetIdentifier != nil ? "Your video was saved to Photos. Tap to open Photos." : "Tap to choose where to save your file in Files."
        content.sound = .default
        content.userInfo = ["kind": "download", "recordID": record.id, "uid": record.uid]
        try? await center.add(UNNotificationRequest(identifier: "vortex-" + record.id, content: content, trigger: nil))
    }
}
