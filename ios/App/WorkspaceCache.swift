import Foundation
import CryptoKit

// Only public application code is stored here. Tokens, API responses, projects,
// and downloaded user media never enter this cache. Files survive app restarts.
actor WorkspaceCache {
    static let shared = WorkspaceCache()
    struct Asset: Codable, Equatable {
        let path: String
        let sha256: String
        let bytes: Int
    }
    struct Manifest: Codable {
        let schema: Int
        let assets: [Asset]
        func validated() throws -> Manifest {
            guard schema == 1, assets.count == 2,
                  Set(assets.map(\.path)) == Set(["Vision.html", "vortex/offline.html"]),
                  assets.allSatisfy({ $0.bytes > 0 && $0.bytes <= 64 * 1024 * 1024 && $0.sha256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil }) else { throw CacheError.invalid }
            return self
        }
    }
    enum CacheError: Error { case invalid }
    private let directory: URL
    private let session: URLSession
    private var checking = false
    private var lastCheck = Date.distantPast

    init(directory: URL? = nil) {
        self.directory = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("WorkspaceCache", isDirectory: true)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 180
        session = URLSession(configuration: configuration)
    }
    static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    private func manifest() -> Manifest? {
        guard let data = try? Data(contentsOf: directory.appendingPathComponent("current.json")),
              data.count < 16384 else { return nil }
        return try? JSONDecoder().decode(Manifest.self, from: data).validated()
    }
    func cached(vortex: Bool) -> Data? {
        let path = vortex ? "vortex/offline.html" : "Vision.html"
        guard let asset = manifest()?.assets.first(where: { $0.path == path }),
              let data = try? Data(contentsOf: directory.appendingPathComponent(asset.sha256)),
              data.count == asset.bytes, Self.digest(data) == asset.sha256 else { return nil }
        return data
    }
    @discardableResult
    func checkForUpdates(force: Bool = false) async -> Bool {
        guard !checking, force || Date().timeIntervalSince(lastCheck) >= 300 else { return false }
        checking = true; lastCheck = Date()
        defer { checking = false }
        do {
            let url = AppConfiguration.webURL.appendingPathComponent("ios-cache-manifest.json")
            let (data, response) = try await session.data(for: URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
            guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                  response.url == url, data.count < 16384 else { throw CacheError.invalid }
            let next = try JSONDecoder().decode(Manifest.self, from: data).validated()
            let current = manifest()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            var excluded = URLResourceValues(); excluded.isExcludedFromBackup = true
            var folder = directory; try folder.setResourceValues(excluded)
            for asset in next.assets {
                let target = directory.appendingPathComponent(asset.sha256)
                if let existing = try? Data(contentsOf: target), existing.count == asset.bytes, Self.digest(existing) == asset.sha256 { continue }
                let source = AppConfiguration.webURL.appendingPathComponent(asset.path)
                let (temporary, reply) = try await session.download(for: URLRequest(url: source, cachePolicy: .reloadIgnoringLocalCacheData))
                defer { try? FileManager.default.removeItem(at: temporary) }
                guard let http = reply as? HTTPURLResponse, http.statusCode == 200, reply.url == source,
                      (try temporary.resourceValues(forKeys: [.fileSizeKey])).fileSize == asset.bytes else { throw CacheError.invalid }
                let bytes = try Data(contentsOf: temporary)
                guard Self.digest(bytes) == asset.sha256 else { throw CacheError.invalid }
                try bytes.write(to: target, options: .atomic)
            }
            // Commit only a complete, hash-verified release. A failed download
            // leaves the previous release usable. Running pages aren't reloaded.
            try data.write(to: directory.appendingPathComponent("current.json"), options: .atomic)
            let keep = Set(next.assets.map(\.sha256) + ["current.json"])
            for file in (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? [] where !keep.contains(file.lastPathComponent) {
                try? FileManager.default.removeItem(at: file)
            }
            return current?.assets != next.assets
        } catch { return false } // Offline: retain the last complete release.
    }
}
