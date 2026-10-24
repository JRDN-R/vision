import Foundation
import ActivityKit

struct VortexActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var title: String
        var phase: String
        var progress: Double?
        var status: String
        var updatedAt: Double
    }
    var recordID: String
    var jobID: String
}
