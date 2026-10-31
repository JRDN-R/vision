import Foundation

struct VortexClient {
    let serverURL: URL
    let uid: String
    let idToken: () async throws -> String

    init(serverURL: URL = AppConfiguration.serverURL, uid: String,
         idToken: @escaping () async throws -> String = { try await NativeSession.validIDToken() }) {
        self.serverURL = serverURL
        self.uid = uid
        self.idToken = idToken
    }

    func submit(_ record: DownloadRecord) async throws -> VortexJob {
        guard record.uid == uid, let url = URL(string: record.sourceURL), SharedLinks.validated(url) != nil else {
            throw VortexClientError.invalidURL
        }
        return try await request("/api/vortex/jobs", method: "POST", body: [
            "input": record.sourceURL, "kind": "download", "quality": "balanced",
            "downloadMode": record.mode.rawValue, "videoFormat": record.videoFormat,
            "audioFormat": "m4a", "requestId": record.requestID
        ])
    }

    func job(_ id: String) async throws -> VortexJob {
        try await request("/api/vortex/jobs/" + validatedJobID(id))
    }

    func cancel(_ id: String) async throws {
        let _: VortexJob = try await request("/api/vortex/jobs/" + validatedJobID(id) + "/cancel", method: "POST", body: [:])
    }

    func ticket(_ id: String) async throws -> TransferTicket {
        let ticket: TransferTicket = try await request("/api/ios/vortex/" + validatedJobID(id) + "/transfer-ticket", method: "POST", body: [:])
        guard ticket.transportProtocol == "VTX1", ticket.url.scheme == "https", ticket.url.host == serverURL.host,
              ticket.url.port == serverURL.port, ticket.url.user == nil, ticket.url.password == nil,
              ticket.url.path == "/api/ios/vortex/" + id + "/transfer", ticket.url.query == nil, ticket.url.fragment == nil,
              ticket.token.range(of: "^vtf_[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              ticket.expiresAt > Date().timeIntervalSince1970 else { throw VortexClientError.invalidResponse }
        return ticket
    }

    private func validatedJobID(_ id: String) throws -> String {
        guard id.count == 24, id.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else { throw VortexClientError.invalidResponse }
        return id
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> T {
        guard NativeSession.currentUID == uid else { throw VortexClientError.signedOut }
        let token = try await idToken()
        guard NativeSession.currentUID == uid else { throw VortexClientError.signedOut }
        guard let url = URL(string: path, relativeTo: serverURL)?.absoluteURL else { throw VortexClientError.invalidResponse }
        var request = URLRequest(url: url, timeoutInterval: 25)
        request.httpMethod = method
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        let session = URLSession(configuration: configuration)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.data(for: request)
        guard NativeSession.currentUID == uid else { throw VortexClientError.signedOut }
        guard let http = response as? HTTPURLResponse, data.count <= 2 * 1024 * 1024 else { throw VortexClientError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let message = object?["error"] as? String ?? (object?["error"] as? [String: Any])?["message"] as? String
                ?? "FUPCJ Server returned HTTP \(http.statusCode)."
            throw VortexAPIError(message: message, statusCode: http.statusCode)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

/// Shared by the app and extension. The server request starts immediately; iOS
/// owns the resulting HTTP transfer after the extension is dismissed.
enum TransferScheduler {
    static func configuration(identifier: String) -> URLSessionConfiguration {
        let value = URLSessionConfiguration.background(withIdentifier: identifier)
        value.sharedContainerIdentifier = AppConfiguration.appGroupIdentifier
        value.sessionSendsLaunchEvents = true
        value.isDiscretionary = false
        value.waitsForConnectivity = true
        value.allowsCellularAccess = true
        value.timeoutIntervalForRequest = 120
        value.timeoutIntervalForResource = 3 * 60 * 60
        value.httpMaximumConnectionsPerHost = 1
        value.httpShouldSetCookies = false
        return value
    }

    /// Returns a retained session to the caller. A task is never resumed before
    /// its account and session identifier have been recorded atomically.
    static func scheduleNext(uid: String, delegate: URLSessionDelegate? = nil) async throws -> URLSession? {
        guard NativeSession.currentUID == uid else { throw VortexClientError.signedOut }
        let store = DownloadStore.shared
        guard let record = try store.claimNextTransfer(uid: uid), let jobID = record.jobID else { return nil }
        do {
            let ticket = try await VortexClient(uid: uid).ticket(jobID)
            guard NativeSession.currentUID == uid,
                  try store.record(id: record.id)?.status == .starting else { throw VortexClientError.signedOut }
            let identifier = (Bundle.main.bundleIdentifier ?? "Vision") + ".vortex-transfer." + record.id + "." + UUID().uuidString.lowercased()
            let session = URLSession(configuration: configuration(identifier: identifier), delegate: delegate, delegateQueue: .main)
            var request = URLRequest(url: ticket.url)
            request.setValue(ticket.token, forHTTPHeaderField: "X-Vision-Transfer")
            request.setValue("application/vnd.vision.vortex-transfer", forHTTPHeaderField: "Accept")
            request.cachePolicy = .reloadIgnoringLocalCacheData
            let task = session.downloadTask(with: request)
            task.taskDescription = record.id
            try store.update(id: record.id) { current in
                current.sessionIdentifier = identifier
                current.transferStartedAt = Date()
                current.status = .processing
                current.phase = "FUPCJ Server is preparing your file"
                current.lastError = nil
                current.bytesReceived = 0
                current.failureNotificationAt = nil
            }
            task.resume()
            return session
        } catch {
            let failed = try? store.update(id: record.id) { current in
                guard current.status != .cancelled else { return }
                current.sessionIdentifier = nil
                current.status = .failed
                current.phase = "Open Vision to retry the transfer"
                current.lastError = error.localizedDescription
            }
            if let failed { await CompletionNotification.sendFailure(failed) }
            throw error
        }
    }

    /// Safely fills the server's three-operation queue. Further shared links
    /// remain durable on the phone until a completion or foreground retry.
    static func submitPending(uid: String) async throws {
        let client = VortexClient(uid: uid)
        for record in try DownloadStore.shared.records(uid: uid).reversed() where record.status == .pending {
            do {
                let job = try await client.submit(record)
                guard NativeSession.currentUID == uid else { throw VortexClientError.signedOut }
                try DownloadStore.shared.update(id: record.id) { current in
                    guard current.status == .pending else { return }
                    current.jobID = job.id
                    current.title = job.title?.isEmpty == false ? job.title! : current.title
                    current.status = .queued
                    current.phase = job.phase
                    current.progress = job.normalizedProgress
                    current.lastError = nil
                }
            } catch let error as VortexAPIError where error.statusCode == 429 {
                try DownloadStore.shared.update(id: record.id) { $0.phase = "Waiting for space in the server queue" }
                break
            } catch {
                let failed = try? DownloadStore.shared.update(id: record.id) { current in
                    current.status = .failed
                    current.phase = "Could not queue this link"
                    current.lastError = error.localizedDescription
                }
                if let failed { await CompletionNotification.sendFailure(failed) }
                if (error as? VortexAPIError)?.statusCode == 401 || NativeSession.currentUID != uid { throw error }
            }
        }
    }
}
