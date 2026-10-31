import Foundation
import ActivityKit
import UIKit

@MainActor
final class LiveActivityManager {
    static let shared = LiveActivityManager()
    private var tokenTasks: [String: Task<Void, Never>] = [:]
    private var updating = Set<String>()

    func update(_ record: DownloadRecord, allowStart: Bool = false) async {
        // Delegate callbacks may have captured an active record before it was
        // failed, cancelled, retried or removed. Always consult durable state.
        guard !updating.contains(record.id) else { return }
        updating.insert(record.id)
        defer { updating.remove(record.id) }
        let stored: DownloadRecord?
        do { stored = try DownloadStore.shared.record(id: record.id) }
        catch { return } // Unavailable storage is not evidence of deletion.
        guard let current = stored,
              NativeSession.currentUID == current.uid, current.status.isActive else {
            await end(recordID: record.id)
            return
        }
        let record = current
        let state = VortexActivityAttributes.ContentState(title: String(record.title.prefix(100)), phase: String(record.phase.prefix(160)),
                                                         progress: record.progress, status: record.status.rawValue, updatedAt: Date().timeIntervalSince1970)
        let content = ActivityContent(state: state, staleDate: Date().addingTimeInterval(75))
        let existing = Activity<VortexActivityAttributes>.activities.first {
            $0.attributes.recordID == record.id && $0.attributes.jobID == record.jobID
                && ($0.activityState == .active || $0.activityState == .stale)
        }
        if let existing {
            if record.liveActivityJobID != record.jobID {
                try? DownloadStore.shared.update(id: record.id) { $0.liveActivityJobID = record.jobID }
            }
            await existing.update(content)
            observePushToken(for: existing, record: record)
        } else if allowStart, record.canStartLiveActivity, UIApplication.shared.applicationState == .active,
                  ActivityAuthorizationInfo().areActivitiesEnabled, let jobID = record.jobID {
            do {
                let activity = try Activity<VortexActivityAttributes>.request(
                    attributes: .init(recordID: record.id, jobID: jobID), content: content,
                    pushType: AppConfiguration.pushEnabled ? .token : nil)
                // A user/system dismissal is final for this attempt. Polling
                // must not make the same activity pop back up every 8 seconds.
                try? DownloadStore.shared.update(id: record.id) { $0.liveActivityJobID = jobID }
                observePushToken(for: activity, record: record)
            } catch {
                // Downloads continue when the user disables Live Activities or
                // iOS declines a new activity. Never invent a progress display.
            }
        }
    }

    /// ActivityKit survives process exits and deleted download rows. Sweep its
    /// inventory even when there are no active downloads left to poll.
    func reconcile() async {
        guard let records = try? DownloadStore.shared.records() else { return }
        let activities = Activity<VortexActivityAttributes>.activities
        let obsolete = LiveActivityReconciliation.obsoleteIDs(
            activities.map { .init(id: $0.id, recordID: $0.attributes.recordID, jobID: $0.attributes.jobID,
                                   isActive: $0.activityState == .active || $0.activityState == .stale) },
            records: records, uid: NativeSession.currentUID)
        for activity in activities where obsolete.contains(activity.id) {
            tokenTasks.removeValue(forKey: activity.attributes.recordID)?.cancel()
            notifyEnded(activity)
            await activity.end(nil, dismissalPolicy: .immediate)
        }
    }

    func end(recordID: String) async {
        tokenTasks.removeValue(forKey: recordID)?.cancel()
        for activity in Activity<VortexActivityAttributes>.activities where activity.attributes.recordID == recordID {
            notifyEnded(activity)
            await activity.end(nil, dismissalPolicy: .immediate)
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

enum LiveActivityReconciliation {
    struct Entry {
        let id: String
        let recordID: String
        let jobID: String
        let isActive: Bool
    }

    static func obsoleteIDs(_ activities: [Entry], records: [DownloadRecord], uid: String?) -> Set<String> {
        let recordsByID = Dictionary(records.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var retained = Set<String>()
        return Set(activities.compactMap { activity in
            guard let record = recordsByID[activity.recordID], record.uid == uid,
                  record.status.isActive, record.jobID == activity.jobID, activity.isActive,
                  retained.insert(record.id).inserted else { return activity.id }
            return nil
        })
    }
}
