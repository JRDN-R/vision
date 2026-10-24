import XCTest
@testable import Vision

final class SharedLinksTests: XCTestCase {
    func testURLAndTextLinksDeduplicateWithoutAcceptingLocalFiles() {
        let result = SharedLinks.extract(from: "Look at https://example.com/video?q=1 and https://example.com/video?q=1 then file:///private/a.mov")
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first?.host, "example.com")
        XCTAssertNil(SharedLinks.validated(URL(string: "file:///private/movie.mp4")!))
        XCTAssertNil(SharedLinks.validated(URL(string: "javascript:alert(1)")!))
        XCTAssertNil(SharedLinks.validated(URL(string: "https://user:password@example.com/video")!))
    }

    func testInputAndLinkCountAreBounded() {
        let many = (0..<20).map { "https://example.com/video/\($0)" }.joined(separator: "\n")
        XCTAssertEqual(SharedLinks.extract(from: many).count, 10)
        XCTAssertTrue(SharedLinks.extract(from: String(repeating: "a", count: 131_073)).isEmpty)
    }

    func testAutomaticAudioDetectionDoesNotMatchLookalikeHosts() {
        XCTAssertEqual(DownloadSelection.automatic.resolved(for: URL(string: "https://open.spotify.com/track/abc")!), .audio)
        XCTAssertEqual(DownloadSelection.automatic.resolved(for: URL(string: "https://spotify.link/abc")!), .audio)
        XCTAssertEqual(DownloadSelection.automatic.resolved(for: URL(string: "https://example.com/music.mp3")!), .audio)
        XCTAssertEqual(DownloadSelection.automatic.resolved(for: URL(string: "https://open.spotify.com.evil.example/track/abc")!), .video)
        XCTAssertEqual(DownloadSelection.video.resolved(for: URL(string: "https://open.spotify.com/track/abc")!), .video)
    }
}
