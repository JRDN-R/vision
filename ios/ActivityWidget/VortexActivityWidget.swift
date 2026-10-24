import ActivityKit
import WidgetKit
import SwiftUI

@main
struct VortexWidgets: WidgetBundle {
    var body: some Widget { VortexLiveActivity() }
}

struct VortexLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: VortexActivityAttributes.self) { context in
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.down.circle.fill").foregroundStyle(.green)
                    Text("Vortex").font(.headline)
                    Spacer()
                    if let value = context.state.progress { Text(value, format: .percent.precision(.fractionLength(0))).monospacedDigit() }
                }
                Text(context.state.title).font(.subheadline).lineLimit(1)
                Text(context.isStale ? "Open Vision to refresh progress" : context.state.phase)
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                if let value = context.state.progress { ProgressView(value: value).tint(.green) }
                else { ProgressView().tint(.green) }
            }
            .padding()
            .activityBackgroundTint(.black.opacity(0.9))
            .activitySystemActionForegroundColor(.white)
            .widgetURL(URL(string: "visioncompanion://downloads"))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label("Vortex", systemImage: "arrow.down.circle.fill").foregroundStyle(.green).font(.headline)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    if let value = context.state.progress { Text(value, format: .percent.precision(.fractionLength(0))).monospacedDigit() }
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(context.state.title).lineLimit(1)
                        Text(context.isStale ? "Open Vision to refresh" : context.state.phase).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        if let value = context.state.progress { ProgressView(value: value).tint(.green) }
                    }
                }
            } compactLeading: {
                Image(systemName: "arrow.down.circle.fill").foregroundStyle(.green)
            } compactTrailing: {
                if context.isStale { Image(systemName: "clock").font(.caption) }
                else if let value = context.state.progress { Text(value, format: .percent.precision(.fractionLength(0))).font(.caption2).monospacedDigit() }
                else { ProgressView().tint(.green) }
            } minimal: {
                Image(systemName: "arrow.down.circle.fill").foregroundStyle(.green)
            }
            .widgetURL(URL(string: "visioncompanion://downloads"))
            .keylineTint(.green)
        }
    }
}
