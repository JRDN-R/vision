import XCTest
@testable import Vision

final class TransferArchiveTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func archive(filename: String = "A song.m4a", mime: String = "audio/mp4", size: Int64? = nil,
                         payload: Data = Data([0, 255, 10, 13, 1, 2, 3]), prefix: String = "") throws -> URL {
        var bytes = Data("VTX1\n".utf8)
        bytes.append(Data(prefix.utf8))
        bytes.append(try JSONSerialization.data(withJSONObject: ["type": "file", "filename": filename,
                                                                "mimeType": mime, "size": size ?? Int64(payload.count)]))
        bytes.append(10)
        bytes.append(payload)
        let url = root.appendingPathComponent(UUID().uuidString + ".vtx")
        try bytes.write(to: url)
        return url
    }

    func testHeartbeatsAreRemovedAndBinaryPayloadIsPreserved() throws {
        let payload = Data((0..<65536).map { UInt8($0 % 256) })
        let input = try archive(payload: payload, prefix: "{\"type\":\"progress\",\"phase\":\"queued\"}\n{\"type\":\"progress\",\"phase\":\"converting\"}\n")
        let result = try TransferArchive.unpack(input, into: root)
        XCTAssertEqual(result.filename, "A song.m4a")
        XCTAssertEqual(result.size, Int64(payload.count))
        XCTAssertTrue(result.isAudio)
        XCTAssertFalse(result.isVideo)
        XCTAssertEqual(try Data(contentsOf: result.url), payload)
    }

    func testTruncatedAndAppendedPayloadsAreRejected() throws {
        let short = try archive(size: 100)
        XCTAssertThrowsError(try TransferArchive.unpack(short, into: root))
        let long = try archive(size: 1)
        XCTAssertThrowsError(try TransferArchive.unpack(long, into: root))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("A song.m4a").path))
    }

    func testUnsafeNamesAndMismatchedFormatsAreRejected() throws {
        for name in ["../escape.m4a", "/tmp/escape.m4a", "folder\\escape.m4a", "hidden\u{0000}.m4a", ".hidden.m4a"] {
            let input = try archive(filename: name)
            XCTAssertThrowsError(try TransferArchive.unpack(input, into: root), name)
        }
        let mismatched = try archive(filename: "movie.mp4", mime: "audio/mp4")
        XCTAssertThrowsError(try TransferArchive.unpack(mismatched, into: root))
    }

    func testUnboundedMetadataAndServerErrorAreRejected() throws {
        let oversized = try archive(prefix: "{\"type\":\"progress\",\"message\":\"" + String(repeating: "x", count: 9000) + "\"}\n")
        XCTAssertThrowsError(try TransferArchive.unpack(oversized, into: root))
        let serverError = try archive(prefix: "{\"type\":\"error\",\"message\":\"Source is unavailable\"}\n")
        XCTAssertThrowsError(try TransferArchive.unpack(serverError, into: root)) { error in
            XCTAssertTrue(error.localizedDescription.contains("Source is unavailable"))
        }
        let tooLarge = try archive(size: 2 * 1024 * 1024 * 1024 + 1)
        XCTAssertThrowsError(try TransferArchive.unpack(tooLarge, into: root))
    }

    func testMediaHeaderMustBePresentAndComplete() throws {
        for text in ["<html>Sign in</html>", "VTX1\n", "VTX1\n{\"type\":\"file\"}", "VTX1\n{\"type\":\"unexpected\"}\n"] {
            let input = root.appendingPathComponent(UUID().uuidString + ".vtx")
            try Data(text.utf8).write(to: input)
            XCTAssertThrowsError(try TransferArchive.unpack(input, into: root))
        }
    }

    func testMOVAndGalleryFilesRemainDifferentKinds() throws {
        let movie = try TransferArchive.unpack(archive(filename: "Movie.mov", mime: "video/quicktime"), into: root)
        XCTAssertTrue(movie.isVideo)
        let gallery = try TransferArchive.unpack(archive(filename: "Gallery.zip", mime: "application/zip"), into: root)
        XCTAssertFalse(gallery.isAudio)
        XCTAssertFalse(gallery.isVideo)
    }
}
