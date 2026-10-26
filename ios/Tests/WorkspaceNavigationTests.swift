import XCTest
import WebKit
@testable import Vision

final class WorkspaceNavigationTests: XCTestCase {
    func testActualLaunchAndWorkspaceDirectoryURLsAreTrusted() {
        // Regression: Foundation URL.path strips directory slashes. Every one
        // of these public launch/directory URLs was rejected by the old check.
        XCTAssertTrue(AppConfiguration.isTrustedPage(AppConfiguration.webURL))
        XCTAssertTrue(AppConfiguration.isTrustedPage(AppConfiguration.workspaceURL))
        for path in ["venture/", "vortex/"] {
            XCTAssertTrue(AppConfiguration.isTrustedPage(AppConfiguration.webURL.appendingPathComponent(path)), path)
        }
        XCTAssertEqual(AppConfiguration.workspaceURL.lastPathComponent, "Vision.html")
    }

    func testDirectoryIndexQueryAndFragmentForms() {
        let base = AppConfiguration.webURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        for suffix in ["", "/", "/index.html", "/Vision.html", "/venture", "/venture/", "/venture/index.html", "/vortex", "/vortex/", "/vortex/index.html"] {
            for tail in ["", "?continue=vortex", "#workspace", "?view=venture#workspace"] {
                XCTAssertTrue(AppConfiguration.isTrustedPage(URL(string: base + suffix + tail)), suffix + tail)
            }
        }
    }

    func testOnlyExactTrustedPagesCanReachTheCredentialBridge() {
        let base = AppConfiguration.webURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        for suffix in ["-evil/", "/other.html", "/ios-signin.html", "/vortex/other", "/Vision.html/", "/%56ision.html", "/venture%2f", "/../other/", "/%2e%2e/", "/vortex/../../other/"] {
            XCTAssertFalse(AppConfiguration.isTrustedPage(URL(string: base + suffix)), suffix)
        }
        var parts = URLComponents(url: AppConfiguration.webURL, resolvingAgainstBaseURL: true)!
        parts.scheme = "http"; XCTAssertFalse(AppConfiguration.isTrustedPage(parts.url))
        parts.scheme = "https"; parts.host = "untrusted.example"; XCTAssertFalse(AppConfiguration.isTrustedPage(parts.url))
        parts = URLComponents(url: AppConfiguration.webURL, resolvingAgainstBaseURL: true)!
        parts.user = "someone"; XCTAssertFalse(AppConfiguration.isTrustedPage(parts.url))
        parts.user = nil; parts.port = 8443; XCTAssertFalse(AppConfiguration.isTrustedPage(parts.url))
        XCTAssertFalse(AppConfiguration.isTrustedPage(nil))
        XCTAssertFalse(AppConfiguration.isTrustedPage(URL(string: "file:///vision/Vision.html")))
        XCTAssertFalse(AppConfiguration.isTrustedPage(URL(string: "javascript:alert(1)")))
    }

    @MainActor
    func testContentProcessTerminationStopsSpinnerAndOffersRecovery() {
        let model = VisionWebModel()
        model.bridgeReady = true
        model.webViewWebContentProcessDidTerminate(model.webView)
        XCTAssertFalse(model.loading)
        XCTAssertFalse(model.bridgeReady)
        XCTAssertNotNil(model.loadFailure)
    }

    @MainActor
    func testBothEarlyAndCommittedNavigationFailuresAreVisible() {
        for early in [true, false] {
            let model = VisionWebModel()
            let error = NSError(domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet)
            if early { model.webView(model.webView, didFailProvisionalNavigation: nil, withError: error) }
            else { model.webView(model.webView, didFail: nil, withError: error) }
            XCTAssertFalse(model.loading)
            XCTAssertTrue(model.loadFailure?.contains(String(NSURLErrorNotConnectedToInternet)) == true)
        }
    }
    @MainActor
    func testTerminatedWorkspaceRetriesOnlyWhenSelectedAndActive() async throws {
        let model = VisionWebModel()
        model.webViewWebContentProcessDidTerminate(model.webView)
        let failedGeneration = model.navigationGeneration
        try await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertEqual(model.navigationGeneration, failedGeneration)
        model.setRecoveryActive(true)
        try await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertGreaterThan(model.navigationGeneration, failedGeneration)
        model.setRecoveryActive(false)
        model.webView.stopLoading()
    }

    @MainActor
    func testBackgroundingCancelsScheduledRecovery() async throws {
        let model = VisionWebModel()
        model.setRecoveryActive(true)
        model.webViewWebContentProcessDidTerminate(model.webView)
        let generation = model.navigationGeneration
        model.setRecoveryActive(false)
        try await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertEqual(model.navigationGeneration, generation)
    }

}
