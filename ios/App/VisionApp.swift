import SwiftUI
import UIKit
import UserNotifications

@main
struct VisionApp: App {
    @UIApplicationDelegateAdaptor(VisionAppDelegate.self) private var appDelegate
    var body: some Scene {
        WindowGroup { VisionRootView().preferredColorScheme(.dark) }
    }
}

@MainActor
final class VisionAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private var activityObserver: NSObjectProtocol?
    private var endedObserver: NSObjectProtocol?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        _ = DownloadManager.shared
        activityObserver = NotificationCenter.default.addObserver(forName: Notification.Name("VisionLiveActivityToken"), object: nil, queue: .main) { note in
            let values = note.userInfo as? [String: String] ?? [:]
            Task { @MainActor in await LiveActivityRegistration.register(values) }
        }
        endedObserver = NotificationCenter.default.addObserver(forName: Notification.Name("VisionLiveActivityEnded"), object: nil, queue: .main) { note in
            guard let id = note.userInfo?["activityID"] as? String else { return }
            Task { await LiveActivityRegistration.unregister(id) }
        }
        return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        AccountNotifications.shared.receivedToken(deviceToken)
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // Approval polling and local download alerts still work without APNs.
        AccountNotifications.shared.registrationFailed()
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        guard notification.request.content.userInfo["uid"] as? String == NativeSession.currentUID else { return [] }
        if notification.request.trigger is UNPushNotificationTrigger,
           AccountNotifications.shared.hasSeenApproval(notification.request.content.userInfo) { return [] }
        AccountNotifications.shared.sawApproval(notification.request.content.userInfo)
        return [.banner, .sound, .list]
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier,
              let uid = response.notification.request.content.userInfo["uid"] as? String,
              uid == NativeSession.currentUID else { return }
        let info = response.notification.request.content.userInfo
        if info["kind"] as? String == "upgrade" {
            AccountNotifications.shared.sawApproval(info)
            await AccountNotifications.shared.showApproval()
        } else if let recordID = info["recordID"] as? String {
            DownloadNotificationRoute.shared.recordID = recordID
        }
    }
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String, completionHandler: @escaping () -> Void) {
        DownloadManager.shared.handleEvents(identifier: identifier, completion: completionHandler)
    }
}

enum LiveActivityRegistration {
    static func unregister(_ id: String) async {
        guard AppConfiguration.pushEnabled, id.range(of: "^[A-Za-z0-9_-]{1,160}$", options: .regularExpression) != nil else { return }
        do {
            let token = try await NativeSession.validIDToken()
            var request = URLRequest(url: AppConfiguration.endpoint("/api/ios/live-activities/" + id))
            request.httpMethod = "DELETE"
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
            request.timeoutInterval = 15
            _ = try await URLSession.shared.data(for: request)
        } catch { /* Expiry and account removal also delete server registrations. */ }
    }
    static func register(_ values: [String: String]) async {
        guard AppConfiguration.pushEnabled, Set(values.keys) == ["jobID", "recordID", "activityID", "token"],
              let uid = NativeSession.currentUID else { return }
        do {
            let token = try await NativeSession.validIDToken()
            guard NativeSession.currentUID == uid else { return }
            var request = URLRequest(url: AppConfiguration.endpoint("/api/ios/live-activities"))
            request.httpMethod = "POST"
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: values)
            request.timeoutInterval = 20
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
                await MainActor.run { DownloadManager.shared.errorMessage = "Background Live Activity updates are not configured on FUPCJ Server. Downloads and in-app progress still work." }
                return
            }
        } catch {
            // The activity keeps its stale timestamp if registration is interrupted.
            // A temporary push failure must never cancel a real download.
        }
    }
}

@MainActor
final class DownloadNotificationRoute: ObservableObject {
    static let shared = DownloadNotificationRoute()
    @Published var recordID: String?
}
