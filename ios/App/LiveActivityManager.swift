import Foundation
import ActivityKit
import UIKit

@MainActor
final class LiveActivityManager {
    static let shared = LiveActivityManager()
    private var tokenTasks: [String: Task<Void, Never>] = [:]

    func update(_ record: DownloadRecord, allowStart: Bool = false) async {
        guard NativeSession.currentUID == record.uid else { return }
        let state = VortexActivityAttributes.ContentState(title: String(record.title.prefix(100)), phase: String(record.phase.prefix(160)),
                                                         progress: record.progress, status: record.status.rawValue, updatedAt: Date().timeIntervalSince1970)
        let content = ActivityContent(state: state, staleDate: Date().addingTimeInterval(75))
        let existing = Activity<VortexActivityAttributes>.activities.first { $0.attributes.recordID == record.id }
        if record.status.isTerminal {
            if let existing {
                notifyEnded(existing)
                await existing.end(content, dismissalPolicy: .after(Date().addingTimeInterval(90)))
            }
            tokenTasks.removeValue(forKey: record.id)?.cancel()
            return
        }
        if let existing {
            await existing.update(content)
            observePushToken(for: existing, record: record)
        } else if allowStart, UIApplication.shared.applicationState == .active,
                  ActivityAuthorizationInfo().areActivitiesEnabled, let jobID = record.jobID {
            do {
                let activity = try Activity<VortexActivityAttributes>.request(
                    attributes: .init(recordID: record.id, jobID: jobID), content: content,
                    pushType: AppConfiguration.pushEnabled ? .token : nil)
                observePushToken(for: activity, record: record)
            } catch {
                // Downloads continue when the user disables Live Activities or
                // iOS declines a new activity. Never invent a progress display.
            }
        }
    }

    func endAll(uid: String? = nil) async {
        let recordIDs = uid.map { Set(((try? DownloadStore.shared.records(uid: $0)) ?? []).map(\.id)) }
        for id in Array(tokenTasks.keys) where recordIDs == nil || recordIDs!.contains(id) {
            tokenTasks.removeValue(forKey: id)?.cancel()
        }
        for activity in Activity<VortexActivityAttributes>.activities where recordIDs == nil || recordIDs!.contains(activity.attributes.recordID) {
            notifyEnded(activity)
            await activity.end(nil, dismissalPolicy: .immediate)
        }
    }

    private func notifyEnded(_ activity: Activity<VortexActivityAttributes>) {
        guard AppConfiguration.pushEnabled else { return }
        NotificationCenter.default.post(name: Notification.Name("VisionLiveActivityEnded"), object: nil,
                                        userInfo: ["activityID": activity.id, "recordID": activity.attributes.recordID,
                                                   "jobID": activity.attributes.jobID])
    }

    private func observePushToken(for activity: Activity<VortexActivityAttributes>, record: DownloadRecord) {
        guard AppConfiguration.pushEnabled, tokenTasks[record.id] == nil else { return }
        tokenTasks[record.id] = Task {
            for await bytes in activity.pushTokenUpdates {
                guard !Task.isCancelled, NativeSession.currentUID == record.uid, let jobID = record.jobID else { break }
                let token = bytes.map { String(format: "%02x", $0) }.joined()
                // Only the app's server receives a scoped ActivityKit token.
                // APNs signing credentials remain on FUPCJ Server.
                NotificationCenter.default.post(name: Notification.Name("VisionLiveActivityToken"), object: nil,
                                                userInfo: ["jobID": jobID, "recordID": record.id, "token": token,
                                                           "activityID": activity.id])
            }
        }
    }
}
