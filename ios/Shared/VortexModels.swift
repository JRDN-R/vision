import Foundation

enum DownloadSelection: String, CaseIterable, Identifiable {
    case automatic, video, audio
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
    func resolved(for url: URL) -> DownloadMode {
        if self == .audio { return .audio }
        if self == .video { return .video }
        let host = url.host?.lowercased() ?? ""
        if ["open.spotify.com", "www.open.spotify.com", "spotify.link", "spoti.fi", "music.youtube.com"].contains(host) {
            return .audio
        }
        if ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"].contains(url.pathExtension.lowercased()) { return .audio }
        return .video
    }
}

enum DownloadMode: String, Codable { case video, audio }

enum DownloadStatus: String, Codable {
    case pending, queued, starting, processing, transferring, ready, saved, failed, cancelled
    var isActive: Bool { [.pending, .queued, .starting, .processing, .transferring].contains(self) }
    var isTerminal: Bool { [.ready, .saved, .failed, .cancelled].contains(self) }
}

struct DownloadRecord: Codable, Identifiable, Equatable {
    var id: String
    var uid: String
    var jobID: String?
    var sourceURL: String
    var title: String
    var mode: DownloadMode
    var videoFormat: String
    var status: DownloadStatus
    var phase: String
    var progress: Double?
    var bytesReceived: Int64
    var localFilename: String?
    var lastError: String?
    var sessionIdentifier: String?
    var createdAt: Date
    var updatedAt: Date
    var savedAssetIdentifier: String?
    var pendingArchiveFilename: String?
    var completionNotificationPending: Bool?
    var completionNotificationAt: Date?
    var notificationError: String?
    var photoSaveAttemptAt: Date?
    var photoSaveAttempts: Int?
    var retryAttempt: Int

    init(uid: String, url: URL, selection: DownloadSelection, videoFormat: String) {
        id = UUID().uuidString.lowercased()
        self.uid = uid
        sourceURL = url.absoluteString
        title = url.host ?? "Vortex download"
        mode = selection.resolved(for: url)
        self.videoFormat = videoFormat == "mov" ? "mov" : "mp4"
        status = .pending
        phase = "Waiting to send to FUPCJ Server"
        bytesReceived = 0
        createdAt = Date()
        updatedAt = createdAt
        retryAttempt = 0
    }

    func shouldRetryPhotoSave(now: Date = Date()) -> Bool {
        status == .ready && mode == .video && savedAssetIdentifier == nil && ["mp4", "mov"].contains(((localFilename ?? "") as NSString).pathExtension.lowercased())
            && (photoSaveAttempts ?? 0) < 3 && now.timeIntervalSince(photoSaveAttemptAt ?? .distantPast) >= 60
    }

    var requestID: String { "ios-" + id + "-" + String(retryAttempt) }
    var suggestedFilename: String { localFilename ?? (title + "." + (mode == .audio ? "m4a" : videoFormat)) }
    var canRetry: Bool { status == .failed || status == .pending }
    var canExport: Bool { localFilename != nil && [.ready, .saved].contains(status) }
}

struct VortexJob: Decodable {
    let id: String
    let status: String
    let phase: String
    let progress: Double?
    let title: String?
    let filename: String?
    let size: Int64?
    let error: String?
    let resultReady: Bool?
    let input: String?
    let downloadMode: String?
    let videoFormat: String?

    var normalizedProgress: Double? {
        guard let progress, progress.isFinite else { return nil }
        // The existing Vortex API reports percentages from 0 through 100.
        return min(1, max(0, progress / 100))
    }
}

struct TransferTicket: Decodable {
    let url: URL
    let token: String
    let expiresAt: Double
    let transportProtocol: String
    enum CodingKeys: String, CodingKey { case url, token, expiresAt; case transportProtocol = "protocol" }
}

struct VortexAPIError: LocalizedError {
    let message: String
    let statusCode: Int
    var errorDescription: String? { message }
}

enum VortexClientError: LocalizedError {
    case signedOut, invalidURL, invalidResponse, unavailableStorage, invalidArchive(String)
    var errorDescription: String? {
        switch self {
        case .signedOut: return "Open Vision and sign in before sharing a link to Vortex."
        case .invalidURL: return "Share an HTTP or HTTPS media link. Files and items without a link cannot be downloaded by Vortex."
        case .invalidResponse: return "FUPCJ Server returned an unexpected response. Update the server and try again."
        case .unavailableStorage: return "The shared iOS storage is unavailable. Check the App Groups signing setting in Xcode."
        case .invalidArchive(let detail): return "The download could not be verified: " + detail
        }
    }
}

enum SharedLinks {
    static let maximumCount = 10
    static func validated(_ url: URL) -> URL? {
        guard ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              let host = url.host, !host.isEmpty, url.user == nil, url.password == nil,
              url.absoluteString.utf8.count <= 16_384,
              !url.absoluteString.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else { return nil }
        return url
    }
    static func extract(from text: String) -> [URL] {
        guard text.utf8.count <= 128 * 1024 else { return [] }
        var result: [URL] = []
        if let direct = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)), let checked = validated(direct),
           !text.trimmingCharacters(in: .whitespacesAndNewlines).contains(where: { $0.isWhitespace }) { result.append(checked) }
        if let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) {
            for match in detector.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
                if let url = match.url, let checked = validated(url), !result.contains(checked) { result.append(checked) }
                if result.count >= maximumCount { break }
            }
        }
        return Array(result.prefix(maximumCount))
    }
}
