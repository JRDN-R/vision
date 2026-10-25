import Foundation
import Combine
import UIKit
import UserNotifications

@MainActor
final class AccountNotifications: ObservableObject {
    static let shared = AccountNotifications()
    @Published var openApproval = false
    @Published private(set) var approvalMessage = ""
    private var deviceToken = UserDefaults.standard.string(forKey: "approvalDeviceToken")
    private var registeredUID: String?
    private var registeredAt = Date.distantPast
    private var checking = false
    private var requestedPushRegistration = false
    private var signingOutUID: String?
    private let defaults = UserDefaults.standard
    private var installation: String {
        if let value = defaults.string(forKey: "notificationInstallation") { return value }
        let value = UUID().uuidString.lowercased()
        defaults.set(value, forKey: "notificationInstallation")
        return value
    }
    func receivedToken(_ data: Data) {
        let token = data.map { String(format: "%02x", $0) }.joined()
        if token != deviceToken { registeredUID = nil }
        deviceToken = token
        defaults.set(token, forKey: "approvalDeviceToken")
        Task { await refresh() }
    }
    func registrationFailed() { requestedPushRegistration = false }
    private func request(_ path: String, method: String = "GET", body: [String:String]? = nil) async throws -> Data {
        let uid = NativeSession.currentUID
        let token = try await NativeSession.validIDToken()
        guard uid == NativeSession.currentUID else { throw NativeSessionError.accountChanged }
        var request = URLRequest(url: AppConfiguration.endpoint(path))
        request.httpMethod = method
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 15
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              uid == NativeSession.currentUID else { throw NativeSessionError.invalidResponse }
        return data
    }
    func unregister() async {
        guard let uid = NativeSession.currentUID, signingOutUID != uid else { return }
        signingOutUID = uid
        defer { if signingOutUID == uid { signingOutUID = nil } }
        registeredUID = nil
        openApproval = false
        UNUserNotificationCenter.current().removeAllDeliveredNotifications()
        UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
        if let deviceToken {
            _ = try? await request("/api/ios/notification-device", method: "DELETE", body: ["token":deviceToken,"installation":installation])
        }
    }
    func hasSeenApproval(_ info: [AnyHashable:Any]) -> Bool {
        guard let uid = info["uid"] as? String, let decision = info["decisionID"] as? Int else { return false }
        return decision <= defaults.integer(forKey: "approvalSeen:" + uid)
    }
    func sawApproval(_ info: [AnyHashable:Any]) {
        guard let uid = info["uid"] as? String, uid == NativeSession.currentUID,
              let decision = info["decisionID"] as? Int else { return }
        defaults.set(max(decision, defaults.integer(forKey: "approvalSeen:" + uid)), forKey: "approvalSeen:" + uid)
    }
    func refresh() async {
        guard !checking, let uid = NativeSession.currentUID, signingOutUID != uid else { return }
        checking = true
        defer { checking = false }
        if AppConfiguration.pushEnabled {
            if !requestedPushRegistration {
                requestedPushRegistration = true
                UIApplication.shared.registerForRemoteNotifications()
            }
            if let deviceToken, registeredUID != uid || Date().timeIntervalSince(registeredAt) > 86400 {
                do {
                    _ = try await request("/api/ios/notification-device", method: "PUT", body: ["token":deviceToken,"installation":installation])
                    registeredUID = uid; registeredAt = Date()
                } catch { /* Free foreground polling continues if push is unavailable. */ }
            }
        }
        do {
            let data = try await request("/api/account/upgrade")
            guard NativeSession.currentUID == uid, signingOutUID != uid,
                  let result = try JSONSerialization.jsonObject(with: data) as? [String:Any],
                  result["approved"] as? Bool == true, let decision = result["decisionID"] as? Int, decision > 0 else { return }
            let center = UNUserNotificationCenter.current()
            for notification in await center.deliveredNotifications() { sawApproval(notification.request.content.userInfo) }
            guard decision > defaults.integer(forKey: "approvalSeen:" + uid) else { return }
            let settings = await center.notificationSettings()
            guard [.authorized,.provisional].contains(settings.authorizationStatus), NativeSession.currentUID == uid, signingOutUID != uid else { return }
            let content = UNMutableNotificationContent()
            content.title = "Account upgrade approved"
            content.body = "Your account now has access to Gemini and Luna."
            content.sound = .default
            content.userInfo = ["kind":"upgrade","uid":uid,"decisionID":decision]
            try await center.add(UNNotificationRequest(identifier: "account-upgrade-" + uid, content: content, trigger: nil))
            defaults.set(decision, forKey: "approvalSeen:" + uid)
        } catch { /* Offline status is never interpreted as approval. */ }
    }

    func showApproval() async {
        guard let uid = NativeSession.currentUID else { return }
        var message = "Your account received an approval notification. Connect to FUPCJ Server to check its current status."
        if let data = try? await request("/api/account/upgrade"),
           let result = try? JSONSerialization.jsonObject(with: data) as? [String:Any] {
            message = result["approved"] as? Bool == true
                ? "Your account is upgraded. Gemini and Luna are available across all your projects. Your saved preferences still apply."
                : "Your account upgrade is no longer active. Open Account Upgrade to check your request."
        }
        guard NativeSession.currentUID == uid, signingOutUID != uid else { return }
        approvalMessage = message
        openApproval = true
    }
}
