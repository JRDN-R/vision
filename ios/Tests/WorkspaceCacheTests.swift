import XCTest
import WebKit
@testable import Vision

final class WorkspaceCacheTests: XCTestCase {
    func testManifestRejectsForeignPathsDuplicateAssetsAndInvalidHashes() throws {
        let good = WorkspaceCache.Asset(path: "Vision.html", sha256: String(repeating: "a", count: 64), bytes: 10)
        let vortex = WorkspaceCache.Asset(path: "vortex/offline.html", sha256: String(repeating: "b", count: 64), bytes: 20)
        XCTAssertNoThrow(try WorkspaceCache.Manifest(schema: 1, assets: [good, vortex]).validated())
        for bad in ["../account.json", "https://untrusted.example/app", "vortex/index.html"] {
            XCTAssertThrowsError(try WorkspaceCache.Manifest(schema: 1, assets: [good, .init(path: bad, sha256: vortex.sha256, bytes: 20)]).validated())
        }
        XCTAssertThrowsError(try WorkspaceCache.Manifest(schema: 1, assets: [good, good]).validated())
        XCTAssertThrowsError(try WorkspaceCache.Manifest(schema: 1, assets: [good, .init(path: vortex.path, sha256: "broken", bytes: 20)]).validated())
    }

    func testDiskCacheSurvivesNewInstanceAndRejectsCorruptedFile() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let html = Data("<html>Saved app</html>".utf8)
        let digest = WorkspaceCache.digest(html)
        let manifest = WorkspaceCache.Manifest(schema: 1, assets: [
            .init(path: "Vision.html", sha256: digest, bytes: html.count),
            .init(path: "vortex/offline.html", sha256: digest, bytes: html.count)])
        try html.write(to: root.appendingPathComponent(digest))
        try JSONEncoder().encode(manifest).write(to: root.appendingPathComponent("current.json"))
        let first = await WorkspaceCache(directory: root).cached(vortex: false)
        XCTAssertEqual(first, html)
        let reopened = await WorkspaceCache(directory: root).cached(vortex: true)
        XCTAssertEqual(reopened, html)
        try Data("corrupt".utf8).write(to: root.appendingPathComponent(digest))
        let rejected = await WorkspaceCache(directory: root).cached(vortex: false)
        XCTAssertNil(rejected)
    }

    @MainActor
    func testSavedHTMLKeepsHTTPSOriginAndNativeMessageURL() async throws {
        let loaded = expectation(description: "Cached HTTPS page loaded")
        let message = expectation(description: "Trusted main-frame URL preserved")
        let probe = CachePageProbe(loaded: loaded, message: message)
        let config = WKWebViewConfiguration()
        config.userContentController.add(probe, name: "probe")
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = probe
        let request = URLRequest(url: AppConfiguration.workspaceURL)
        web.loadSimulatedRequest(request, responseHTML: "<html><body><script>window.webkit.messageHandlers.probe.postMessage(location.origin);</script></body></html>")
        await fulfillment(of: [loaded, message], timeout: 10)
        XCTAssertEqual(web.url, AppConfiguration.workspaceURL)
        XCTAssertEqual(probe.origin, "https://" + AppConfiguration.webURL.host!)
        XCTAssertTrue(AppConfiguration.isTrustedPage(probe.frameURL))
    }
}

@MainActor
private final class CachePageProbe: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
    let loaded: XCTestExpectation
    let message: XCTestExpectation
    var origin: String?
    var frameURL: URL?
    init(loaded: XCTestExpectation, message: XCTestExpectation) { self.loaded = loaded; self.message = message }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { loaded.fulfill() }
    func userContentController(_ userContentController: WKUserContentController, didReceive value: WKScriptMessage) {
        origin = value.body as? String; frameURL = value.frameInfo.request.url; message.fulfill()
    }
}
