import UIKit
import UniformTypeIdentifiers

/// Selecting Vortex in Share starts a server job and a system-owned download.
/// No private APIs or responder-chain tricks are used to launch the main app.
final class ShareViewController: UIViewController {
    private let headline = UILabel()
    private let detail = UILabel()
    private let spinner = UIActivityIndicatorView(style: .large)
    private let doneButton = UIButton(type: .system)
    private let retryButton = UIButton(type: .system)
    private var retainedSessions: [URLSession] = []
    private let transferDelegate = ShareTransferDelegate()
    private var submissionTask: Task<Void, Never>?
    private var recordIDs: [String] = []

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.055, green: 0.065, blue: 0.06, alpha: 1)
        let icon = UIImageView(image: UIImage(systemName: "arrow.down.circle.fill"))
        icon.tintColor = .systemGreen
        icon.contentMode = .scaleAspectFit
        icon.heightAnchor.constraint(equalToConstant: 46).isActive = true
        headline.font = .preferredFont(forTextStyle: .title2)
        headline.textColor = .white
        headline.textAlignment = .center
        headline.numberOfLines = 0
        headline.text = "Sending to Vortex"
        detail.font = .preferredFont(forTextStyle: .subheadline)
        detail.textColor = .lightGray
        detail.textAlignment = .center
        detail.numberOfLines = 0
        detail.text = "Balanced quality · MP4 video or M4A audio"
        spinner.color = .systemGreen
        spinner.startAnimating()
        doneButton.setTitle("Done", for: .normal)
        doneButton.addTarget(self, action: #selector(finish), for: .touchUpInside)
        doneButton.isHidden = true
        retryButton.setTitle("Try again", for: .normal)
        retryButton.addTarget(self, action: #selector(retry), for: .touchUpInside)
        retryButton.isHidden = true
        let stack = UIStackView(arrangedSubviews: [icon, headline, detail, spinner, retryButton, doneButton])
        stack.axis = .vertical
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 28),
            stack.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -28),
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 30),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -24)
        ])
        preferredContentSize = CGSize(width: 380, height: 330)
        submissionTask = Task { @MainActor in await start() }
    }

    @MainActor
    private func start() async {
        do {
            guard let uid = NativeSession.currentUID else { throw VortexClientError.signedOut }
            let urls = await sharedURLs()
            guard !Task.isCancelled else { return }
            guard !urls.isEmpty else { throw VortexClientError.invalidURL }
            let defaults = UserDefaults(suiteName: AppConfiguration.appGroupIdentifier)
            let format = defaults?.string(forKey: "vortexVideoFormat") == "mov" ? "mov" : "mp4"
            let records = urls.map { DownloadRecord(uid: uid, url: $0, selection: .automatic, videoFormat: format) }
            try DownloadStore.shared.insert(records)
            recordIDs = records.map(\.id)
            try await submit(uid: uid)
        } catch { showError(error) }
    }

    @MainActor
    private func submit(uid: String) async throws {
        headline.text = "Sending to FUPCJ Server"
        spinner.startAnimating()
        doneButton.isHidden = true
        retryButton.isHidden = true
        try await TransferScheduler.submitPending(uid: uid)
        if let session = try await TransferScheduler.scheduleNext(uid: uid, delegate: transferDelegate) { retainedSessions.append(session) }
        let records = try DownloadStore.shared.records(uid: uid).filter { recordIDs.contains($0.id) }
        let accepted = records.filter { $0.jobID != nil }.count
        let pending = records.filter { $0.status == .pending }.count
        let failed = records.filter { $0.status == .failed }
        spinner.stopAnimating()
        doneButton.isHidden = false
        if accepted > 0 {
            headline.text = accepted == 1 ? "Vortex download started" : "\(accepted) Vortex downloads started"
            if pending > 0 {
                detail.text = "FUPCJ Server is processing your links. \(pending) more are saved in Vision waiting for queue space. Open Vision to send those links and see progress."
            } else if !failed.isEmpty {
                detail.text = "\(accepted) accepted. \(failed.count) could not start. Open Vision Downloads to retry them."
            } else {
                detail.text = "FUPCJ Server keeps working after this closes. Videos save to Photos if access is enabled. Open Vision for Dynamic Island progress and to choose where audio saves in Files."
            }
            // The download task was resumed and its shared record committed
            // before completing the extension request.
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in self?.finish() }
        } else if pending > 0 {
            headline.text = "Links saved in Vision"
            detail.text = "The server queue is full. Open Vision to send these links when a slot becomes available."
        } else {
            throw VortexAPIError(message: failed.first?.lastError ?? "The server could not accept this link.", statusCode: 400)
        }
    }

    @MainActor
    private func sharedURLs() async -> [URL] {
        var values: [URL] = []
        for item in (extensionContext?.inputItems as? [NSExtensionItem]) ?? [] {
            if let text = item.attributedContentText?.string { values.append(contentsOf: SharedLinks.extract(from: text)) }
            for provider in (item.attachments ?? []).prefix(20) {
                if Task.isCancelled { return [] }
                if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                    if let value = try? await itemValue(provider, type: UTType.url.identifier) {
                        if let url = value as? URL, let checked = SharedLinks.validated(url) { values.append(checked) }
                        else if let text = value as? String { values.append(contentsOf: SharedLinks.extract(from: text)) }
                    }
                } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                    if let value = try? await itemValue(provider, type: UTType.plainText.identifier) {
                        if let text = value as? String { values.append(contentsOf: SharedLinks.extract(from: text)) }
                        else if let text = value as? NSAttributedString { values.append(contentsOf: SharedLinks.extract(from: text.string)) }
                        else if let data = value as? Data, data.count <= 128 * 1024, let text = String(data: data, encoding: .utf8) {
                            values.append(contentsOf: SharedLinks.extract(from: text))
                        }
                    }
                }
                if values.count >= SharedLinks.maximumCount { break }
            }
            if values.count >= SharedLinks.maximumCount { break }
        }
        var seen = Set<String>()
        return Array(values.filter { seen.insert($0.absoluteString).inserted }.prefix(SharedLinks.maximumCount))
    }

    private func itemValue(_ provider: NSItemProvider, type: String) async throws -> NSSecureCoding? {
        try await withCheckedThrowingContinuation { continuation in
            provider.loadItem(forTypeIdentifier: type, options: nil) { value, error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume(returning: value) }
            }
        }
    }

    @MainActor
    private func showError(_ error: Error) {
        spinner.stopAnimating()
        headline.text = "Vortex needs your attention"
        detail.text = error.localizedDescription
        doneButton.isHidden = false
        retryButton.isHidden = recordIDs.isEmpty || NativeSession.currentUID == nil
    }

    @objc private func retry() {
        submissionTask = Task { @MainActor in
            guard let uid = NativeSession.currentUID else { showError(VortexClientError.signedOut); return }
            do {
                for id in recordIDs {
                    try DownloadStore.shared.update(id: id) { current in
                        if current.status == .failed {
                            current.status = current.jobID == nil ? .pending : .queued
                            current.sessionIdentifier = nil
                            current.lastError = nil
                        }
                    }
                }
                try await submit(uid: uid)
            } catch { showError(error) }
        }
    }

    @objc private func finish() {
        submissionTask?.cancel()
        extensionContext?.completeRequest(returningItems: nil, completionHandler: nil)
    }
}

/// If a tiny file finishes before the share UI closes, this delegate receives
/// it here. Once the extension exits, iOS delivers the session to Vision instead.
private final class ShareTransferDelegate: NSObject, URLSessionDownloadDelegate {
    private var followingSessions: [URLSession] = []

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        guard let identifier = session.configuration.identifier,
              let record = try? DownloadStore.shared.record(sessionIdentifier: identifier),
              record.uid == NativeSession.currentUID, record.status != .cancelled else { return }
        let status = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 0
        do {
            guard (200..<300).contains(status) else {
                throw VortexAPIError(message: "The transfer could not start (HTTP \(status)). Open Vision to retry; the server job is preserved.", statusCode: status)
            }
            let archive = try TransferCompletion.preserve(location, record: record)
            Task {
                do {
                    if let final = try await TransferCompletion.finish(recordID: record.id, archive: archive) { await CompletionNotification.send(final) }
                    if NativeSession.currentUID == record.uid,
                       let next = try await TransferScheduler.scheduleNext(uid: record.uid, delegate: self) {
                        await MainActor.run { self.followingSessions.append(next) }
                    }
                } catch {
                    // The shared finalizer keeps either a validated local file
                    // or a durable, explicit retry state for the main app.
                }
            }
        } catch {
            try? DownloadStore.shared.update(id: record.id) { current in
                current.status = .failed
                current.phase = "Open Vision to retry the transfer"
                current.lastError = error.localizedDescription
                current.sessionIdentifier = nil
            }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let error, let identifier = session.configuration.identifier,
              let record = try? DownloadStore.shared.record(sessionIdentifier: identifier) else { return }
        try? DownloadStore.shared.update(id: record.id) { current in
            current.sessionIdentifier = nil
            guard current.status != .cancelled else { return }
            current.status = .failed
            current.phase = "Open Vision to retry the transfer"
            current.lastError = error.localizedDescription
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
