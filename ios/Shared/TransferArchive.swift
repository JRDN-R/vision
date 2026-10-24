import Foundation

/// Parses the small framed transport used while FUPCJ Server is still working.
/// The transport is not a media file. Only the verified payload is exported.
enum TransferArchive {
    struct Result {
        let url: URL
        let filename: String
        let mimeType: String
        let size: Int64
        var isVideo: Bool { ["mp4", "mov"].contains(url.pathExtension.lowercased()) && mimeType.hasPrefix("video/") }
        var isAudio: Bool { mimeType.hasPrefix("audio/") }
    }

    private struct Frame: Decodable {
        let type: String
        let filename: String?
        let mimeType: String?
        let size: Int64?
        let message: String?
    }

    private static let maximumPayload: Int64 = 2 * 1024 * 1024 * 1024
    private static let allowedMIMEs: [String: Set<String>] = [
        "mp4": ["video/mp4"], "mov": ["video/quicktime"],
        "m4a": ["audio/mp4", "audio/x-m4a", "audio/m4a"], "mp3": ["audio/mpeg", "audio/mp3"],
        "wav": ["audio/wav", "audio/x-wav", "audio/vnd.wave"],
        "jpg": ["image/jpeg"], "jpeg": ["image/jpeg"], "png": ["image/png"],
        "gif": ["image/gif"], "webp": ["image/webp"], "zip": ["application/zip", "application/x-zip-compressed"]
    ]

    static func unpack(_ archive: URL, into directory: URL) throws -> Result {
        let input = try FileHandle(forReadingFrom: archive)
        defer { try? input.close() }
        let reader = LineReader(handle: input)
        guard try reader.line() == Data("VTX1".utf8) else { throw VortexClientError.invalidArchive("unexpected transport header") }
        var metadata: Frame?
        while let data = try reader.line() {
            let frame: Frame
            do { frame = try JSONDecoder().decode(Frame.self, from: data) }
            catch { throw VortexClientError.invalidArchive("invalid transport metadata") }
            switch frame.type {
            case "progress": continue
            case "error": throw VortexClientError.invalidArchive(String((frame.message ?? "The server could not finish this download.").prefix(600)))
            case "file": metadata = frame
            default: throw VortexClientError.invalidArchive("unknown transport frame")
            }
            if metadata != nil { break }
        }
        guard let metadata, let name = metadata.filename, let mime = metadata.mimeType?.lowercased(),
              let size = metadata.size, size > 0, size <= maximumPayload else {
            throw VortexClientError.invalidArchive("the file header is missing or its size is unsupported")
        }
        guard !name.isEmpty, name.utf8.count <= 1024, name == (name as NSString).lastPathComponent,
              !name.hasPrefix("."), !name.contains("/"), !name.contains("\\"),
              !name.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else {
            throw VortexClientError.invalidArchive("unsafe filename")
        }
        let suffix = (name as NSString).pathExtension.lowercased()
        guard allowedMIMEs[suffix]?.contains(mime) == true else {
            throw VortexClientError.invalidArchive("unsupported file format; the original remains in Vortex on FUPCJ Server")
        }
        let attributes = try FileManager.default.attributesOfItem(atPath: archive.path)
        let total = (attributes[.size] as? NSNumber)?.uint64Value ?? 0
        guard UInt64(size) <= UInt64.max - reader.consumed, total == reader.consumed + UInt64(size) else {
            throw VortexClientError.invalidArchive("the transfer is incomplete or has unexpected extra bytes")
        }
        var stem = String(((name as NSString).deletingPathExtension).prefix(100)).trimmingCharacters(in: .whitespacesAndNewlines)
        while stem.utf8.count > 180 { stem.removeLast() }
        let filename = (stem.isEmpty ? "Vortex" : stem) + "." + suffix
        let destination = directory.appendingPathComponent(filename, isDirectory: false)
        let partial = directory.appendingPathComponent(UUID().uuidString + ".partial", isDirectory: false)
        guard FileManager.default.createFile(atPath: partial.path, contents: nil,
                                            attributes: [.posixPermissions: 0o600, .protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]) else {
            throw VortexClientError.unavailableStorage
        }
        var completed = false
        defer { if !completed { try? FileManager.default.removeItem(at: partial) } }
        let output = try FileHandle(forWritingTo: partial)
        defer { try? output.close() }
        try input.seek(toOffset: reader.consumed)
        var remaining = size
        while remaining > 0 {
            let block = try input.read(upToCount: Int(min(1_048_576, remaining))) ?? Data()
            guard !block.isEmpty else { throw VortexClientError.invalidArchive("the media data ended early") }
            try output.write(contentsOf: block)
            remaining -= Int64(block.count)
        }
        try output.synchronize()
        try output.close()
        if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
        try FileManager.default.moveItem(at: partial, to: destination)
        completed = true
        return Result(url: destination, filename: filename, mimeType: mime, size: size)
    }

    private final class LineReader {
        let handle: FileHandle
        var buffer = Data()
        var consumed: UInt64 = 0
        init(handle: FileHandle) { self.handle = handle }
        func line() throws -> Data? {
            while true {
                if let newline = buffer.firstIndex(of: 10) {
                    let count = buffer.distance(from: buffer.startIndex, to: newline)
                    guard count <= 8 * 1024 else { throw VortexClientError.invalidArchive("metadata line is too large") }
                    let result = Data(buffer.prefix(count))
                    buffer.removeFirst(count + 1)
                    consumed += UInt64(count + 1)
                    guard consumed <= 1_048_576 else { throw VortexClientError.invalidArchive("metadata is too large") }
                    return result
                }
                guard buffer.count <= 8 * 1024 else { throw VortexClientError.invalidArchive("metadata line is too large") }
                let block = try handle.read(upToCount: 4096) ?? Data()
                if block.isEmpty {
                    if buffer.isEmpty { return nil }
                    throw VortexClientError.invalidArchive("an incomplete metadata line was received")
                }
                buffer.append(block)
            }
        }
    }
}
