import Foundation
import Darwin

/// One lock-protected file is shared by the app and share extension. Credentials
/// never enter this file; Firebase credentials stay in the shared Keychain.
final class DownloadStore: @unchecked Sendable {
    static let shared = DownloadStore()
    private let processLock = NSRecursiveLock()

    func rootDirectory() throws -> URL {
        guard let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: AppConfiguration.appGroupIdentifier) else {
            throw VortexClientError.unavailableStorage
        }
        let directory = root.appendingPathComponent("VortexDownloads", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
        return directory
    }

    private func locked<T>(_ body: (URL) throws -> T) throws -> T {
        processLock.lock()
        defer { processLock.unlock() }
        let directory = try rootDirectory()
        let descriptor = open(directory.appendingPathComponent("records.lock").path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw VortexClientError.unavailableStorage }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_EX) == 0 else { throw VortexClientError.unavailableStorage }
        defer { flock(descriptor, LOCK_UN) }
        return try body(directory)
    }

    private func decode(_ directory: URL) throws -> [DownloadRecord] {
        let file = directory.appendingPathComponent("records.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        let data = try Data(contentsOf: file)
        guard data.count <= 8 * 1024 * 1024 else { throw VortexClientError.unavailableStorage }
        return try JSONDecoder().decode([DownloadRecord].self, from: data)
    }

    private func encode(_ values: [DownloadRecord], in directory: URL) throws {
        let file = directory.appendingPathComponent("records.json")
        let data = try JSONEncoder().encode(values)
        guard data.count <= 8 * 1024 * 1024 else {
            throw VortexAPIError(message: "The local download list is full. Remove older items before adding more.", statusCode: 507)
        }
        try data.write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: file.path)
    }

    func records(uid: String? = nil) throws -> [DownloadRecord] {
        try locked { directory in
            try decode(directory).filter { uid == nil || $0.uid == uid }.sorted { $0.createdAt > $1.createdAt }
        }
    }

    func record(id: String) throws -> DownloadRecord? { try records().first { $0.id == id } }
    func record(sessionIdentifier: String) throws -> DownloadRecord? { try records().first { $0.sessionIdentifier == sessionIdentifier } }

    func insert(_ records: [DownloadRecord]) throws {
        try locked { directory in
            var values = try decode(directory)
            guard values.count + records.count <= 1000 else { throw VortexAPIError(message: "Remove some completed downloads before adding more.", statusCode: 507) }
            for record in records where !values.contains(where: { $0.id == record.id }) { values.append(record) }
            try encode(values, in: directory)
        }
    }

    @discardableResult
    func update(id: String, _ update: (inout DownloadRecord) throws -> Void) throws -> DownloadRecord? {
        try locked { directory in
            var values = try decode(directory)
            guard let index = values.firstIndex(where: { $0.id == id }) else { return nil }
            let previous = values[index].status
            try update(&values[index])
            values[index].trackStatusChange(from: previous)
            values[index].updatedAt = Date()
            try encode(values, in: directory)
            return values[index]
        }
    }

    /// App and extension may race. Claiming the slot is atomic across processes.
    func claimNextTransfer(uid: String) throws -> DownloadRecord? {
        try locked { directory in
            var values = try decode(directory)
            guard !values.contains(where: { $0.uid == uid && ($0.sessionIdentifier != nil || $0.status == .starting) }) else { return nil }
            guard let index = values.indices.filter({ values[$0].uid == uid && values[$0].jobID != nil && values[$0].status == .queued })
                .min(by: { values[$0].createdAt < values[$1].createdAt }) else { return nil }
            values[index].status = .starting
            values[index].phase = "Preparing background transfer"
            values[index].updatedAt = Date()
            try encode(values, in: directory)
            return values[index]
        }
    }

    func localURL(for record: DownloadRecord) throws -> URL {
        guard let name = record.localFilename, name == (name as NSString).lastPathComponent,
              !name.hasPrefix("."), !name.contains("/"), !name.contains("\\") else { throw VortexClientError.invalidResponse }
        return try directory(for: record.id).appendingPathComponent(name)
    }

    func directory(for id: String) throws -> URL {
        guard UUID(uuidString: id) != nil else { throw VortexClientError.invalidResponse }
        let path = try rootDirectory().appendingPathComponent(id, isDirectory: true)
        try FileManager.default.createDirectory(at: path, withIntermediateDirectories: true,
                                               attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
        return path
    }

    func remove(id: String) throws {
        try locked { directory in
            var values = try decode(directory)
            guard let record = values.first(where: { $0.id == id }), record.sessionIdentifier == nil else { return }
            values.removeAll { $0.id == id }
            try encode(values, in: directory)
            if UUID(uuidString: id) != nil {
                let path = directory.appendingPathComponent(id, isDirectory: true)
                if FileManager.default.fileExists(atPath: path.path) { try FileManager.default.removeItem(at: path) }
            }
        }
    }
}
