import SwiftUI
import Photos
import UniformTypeIdentifiers
import UIKit

private struct ExportItem: Identifiable {
    let id = UUID()
    let url: URL
    var record: DownloadRecord?
}

struct VisionRootView: View {
    @StateObject private var web = VisionWebModel()
    @ObservedObject private var downloads = DownloadManager.shared
    @Environment(\.scenePhase) private var scenePhase
    @State private var settings = false
    @State private var export: ExportItem?
    @State private var offered = Set<String>()
    @State private var urlText = ""
    @State private var selection = DownloadSelection.automatic
    @AppStorage("vortexVideoFormat", store: UserDefaults(suiteName: AppConfiguration.appGroupIdentifier)) private var videoFormat = "mp4"
    private let green = Color(red: 0.83, green: 0.94, blue: 0.67)

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.08, blue: 0.07).ignoresSafeArea()
                VisionWebView(model: web).opacity(web.page == .downloads ? 0 : 1).allowsHitTesting(web.page != .downloads)
                if web.page == .downloads { downloadsView }
                if web.loading && web.page != .downloads {
                    VStack { ProgressView().tint(green).padding(12).background(.ultraThinMaterial, in: Capsule()); Spacer() }.padding(.top, 6)
                }
            }
            .navigationTitle(web.page.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { web.webView.reload(); Task { await downloads.refresh() } } label: { Image(systemName: "arrow.clockwise") }.accessibilityLabel("Refresh Vision")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { settings = true } label: { Image(systemName: "person.crop.circle") }.accessibilityLabel("Account and download settings")
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                HStack(spacing: 0) {
                    ForEach(VisionPage.allCases) { page in
                        Button { web.select(page) } label: {
                            VStack(spacing: 5) { Image(systemName: page.symbol).font(.system(size: 20)); Text(page.title).font(.caption2) }
                                .foregroundStyle(web.page == page ? green : .secondary)
                                .frame(maxWidth: .infinity).padding(.vertical, 10)
                        }.buttonStyle(.plain).accessibilityAddTraits(web.page == page ? .isSelected : [])
                    }
                }.background(.ultraThinMaterial)
            }
        }
        .tint(green)
        .sheet(isPresented: $settings) { settingsView }
        .sheet(item: $export, onDismiss: { Task { @MainActor in offerAudioExport() } }) { item in
            FilesExporter(url: item.url) { saved in
                if saved, let record = item.record { Task { await downloads.markExported(record) } }
                export = nil
            }
        }
        .alert("Vision", isPresented: Binding(get: { web.errorMessage != nil || downloads.errorMessage != nil }, set: { if !$0 { web.errorMessage = nil; downloads.errorMessage = nil } })) {
            Button("OK") { web.errorMessage = nil; downloads.errorMessage = nil }
        } message: { Text(web.errorMessage ?? downloads.errorMessage ?? "") }
        .onChange(of: downloads.records) { _, _ in offerAudioExport() }
        .onChange(of: web.exportURL) { _, url in if let url { export = ExportItem(url: url); web.exportURL = nil } }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await downloads.refresh(); offerAudioExport() } }
        }
        .onOpenURL { url in if ["visionapp", "visioncompanion"].contains(url.scheme ?? ""), url.host == "downloads" { web.select(.downloads) } }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("VisionOpenDownloads"))) { _ in web.select(.downloads) }
    }

    private var downloadsView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Vortex Downloads").font(.title.bold())
                    Text("Share a media link to Vortex from another app, or paste it here.").foregroundStyle(.secondary)
                }
                if web.email.isEmpty {
                    Button("Continue with Google") { web.select(.vision); Task { await web.signIn() } }.buttonStyle(.borderedProminent)
                    Text("You can also sign in with your existing email and password in Vision.").font(.footnote).foregroundStyle(.secondary)
                } else {
                    VStack(spacing: 12) {
                        TextField("Paste a media URL", text: $urlText, axis: .vertical).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled().padding(13).background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                        HStack {
                            Picker("Media", selection: $selection) { ForEach(DownloadSelection.allCases) { Text($0.label).tag($0) } }.pickerStyle(.menu)
                            Spacer()
                            Text("Balanced · " + videoFormat.uppercased()).font(.caption).foregroundStyle(.secondary)
                            Button("Download") {
                                let urls = SharedLinks.extract(from: urlText)
                                guard !urls.isEmpty else { downloads.errorMessage = "Paste an HTTP or HTTPS media link."; return }
                                urlText = ""
                                Task { await downloads.enqueue(urls: urls, mode: selection, videoFormat: videoFormat) }
                            }.buttonStyle(.borderedProminent).disabled(urlText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }
                    if PHPhotoLibrary.authorizationStatus(for: .addOnly) == .notDetermined {
                        Button { Task { await downloads.requestPhotoAccess() } } label: { Label("Enable automatic video saving to Photos", systemImage: "photo.badge.plus") }.font(.subheadline)
                    }
                }
                let items = downloads.records.filter { $0.uid == NativeSession.currentUID }
                if items.isEmpty {
                    ContentUnavailableView("Your downloads appear here", systemImage: "tray.and.arrow.down", description: Text("FUPCJ Server prepares each file. iOS then transfers it in the background. Open Vision to start Dynamic Island progress or choose an audio destination."))
                } else {
                    ForEach(items.sorted { $0.createdAt > $1.createdAt }) { record in downloadRow(record) }
                }
            }.padding(20)
        }.refreshable { await downloads.refresh() }
    }

    private func downloadRow(_ record: DownloadRecord) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                Image(systemName: record.mode == .audio ? "waveform" : "film").foregroundStyle(green).padding(.top, 3)
                VStack(alignment: .leading, spacing: 5) {
                    Text(record.title).font(.headline).lineLimit(2)
                    Text(record.phase).font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                if record.status == .saved { Image(systemName: "checkmark.circle.fill").foregroundStyle(green) }
            }
            if record.status.isActive {
                if let progress = record.progress { ProgressView(value: progress).tint(green); Text("\(Int(progress * 100))% · " + record.phase).font(.caption).foregroundStyle(.secondary) }
                else { ProgressView().tint(green) }
            }
            if let error = record.lastError { Text(error).font(.footnote).foregroundStyle(.orange) }
            HStack {
                if record.canExport {
                    Button("Save to Files…") { offer(record) }.buttonStyle(.bordered)
                    if ["mp4", "mov"].contains(URL(fileURLWithPath: record.localFilename ?? "").pathExtension.lowercased()), record.savedAssetIdentifier == nil {
                        Button("Save to Photos") { Task { await downloads.requestPhotoAccess(); await downloads.saveToPhotos(record) } }.buttonStyle(.bordered)
                    }
                }
                if record.canRetry { Button("Retry") { Task { await downloads.retry(record) } }.buttonStyle(.bordered) }
                if record.status.isActive { Button("Cancel", role: .destructive) { Task { await downloads.cancel(record) } }.buttonStyle(.borderless) }
                Spacer(minLength: 0)
                if record.status.isTerminal { Button { Task { await downloads.remove(record) } } label: { Image(systemName: "trash") }.accessibilityLabel("Remove local copy") }
            }.font(.caption)
        }.padding(16).background(.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 18))
    }

    private var settingsView: some View {
        NavigationStack {
            Form {
                Section("Your account") {
                    if web.email.isEmpty {
                        Button("Continue with Google") { settings = false; web.select(.vision); Task { await web.signIn() } }
                    } else {
                        Text(web.email)
                        Button("Sign out", role: .destructive) { settings = false; Task { await web.signOut() } }
                    }
                    Text("Vision, Venture, and Vortex use the same account and saved FUPCJ Server projects.").font(.footnote).foregroundStyle(.secondary)
                }
                Section("Shared links") {
                    Picker("Video format", selection: $videoFormat) { Text("MP4").tag("mp4"); Text("MOV").tag("mov") }
                    Text("Balanced video quality. Audio links use M4A and ask where to save in Files when you open Vision.").font(.footnote).foregroundStyle(.secondary)
                    Button("Enable Photos saving") { Task { await downloads.requestPhotoAccess() } }
                    Button("Open iOS app settings") { if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) } }
                }
                Section("Progress") {
                    Text(AppConfiguration.pushEnabled ? "Server Live Activity updates require the matching Apple push configuration on FUPCJ Server." : "Open Vision after sharing to start a Live Activity. With free signing, background progress can become stale until iOS allows the app to update it.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("A share starts server processing and a background transfer. Avoid force-quitting Vision while files transfer. Audio destination choices open when the app is visible.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Vision settings")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { settings = false } } }
        }
    }

    private func offer(_ record: DownloadRecord) {
        do { offered.insert(record.id); export = ExportItem(url: try downloads.exportURL(for: record), record: record) }
        catch { downloads.errorMessage = error.localizedDescription }
    }
    private func offerAudioExport() {
        guard scenePhase == .active, export == nil, !settings else { return }
        if let item = downloads.records.first(where: { $0.uid == NativeSession.currentUID && $0.status == .ready && $0.mode == .audio && $0.canExport && !offered.contains($0.id) }) { offer(item) }
    }
}

private struct FilesExporter: UIViewControllerRepresentable {
    let url: URL
    let completion: (Bool) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(completion: completion) }
    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let controller = UIDocumentPickerViewController(forExporting: [url], asCopy: true)
        controller.delegate = context.coordinator
        return controller
    }
    func updateUIViewController(_ uiViewController: UIDocumentPickerViewController, context: Context) {}
    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let completion: (Bool) -> Void
        init(completion: @escaping (Bool) -> Void) { self.completion = completion }
        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) { completion(!urls.isEmpty) }
        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { completion(false) }
    }
}
