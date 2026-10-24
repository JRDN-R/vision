import SwiftUI
import WebKit
import UniformTypeIdentifiers
import AuthenticationServices

enum VisionPage: String, CaseIterable, Identifiable {
    case vision, venture, vortex, downloads
    var id: String { rawValue }
    var title: String { rawValue.capitalized }
    var symbol: String {
        switch self { case .vision: return "square.grid.2x2"; case .venture: return "bubble.left.and.bubble.right"; case .vortex: return "arrow.down.circle"; case .downloads: return "tray.and.arrow.down" }
    }
}

@MainActor
final class VisionWebModel: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply, WKDownloadDelegate {
    @Published var page: VisionPage = .vision
    @Published var loading = true
    @Published var errorMessage: String?
    @Published var email = (try? NativeSession.load())?.email ?? ""
    @Published var exportURL: URL?
    @Published var bridgeReady = false
    private let google = GoogleSignIn()
    private var authenticating = false
    private var downloadURLs: [ObjectIdentifier: URL] = [:]
    private var lastWebPage: VisionPage = .vision

    lazy var webView: WKWebView = {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        let controller = WKUserContentController()
        controller.addScriptMessageHandler(self, contentWorld: .page, name: "vision")
        let bridge = """
        (() => {
          if (window.top !== window) return;
          const post = body => window.webkit.messageHandlers.vision.postMessage(body);
          Object.defineProperty(window, 'VisionNative', {value: Object.freeze({
            isNative: true,
            signIn: () => post({type:'signIn'}),
            openDownloads: () => post({type:'downloads'}),
            adoptVortexJob: jobID => post({type:'vortexJob',jobID})
          }), configurable:false, writable:false});
        })();
        """
        controller.addUserScript(WKUserScript(source: bridge, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        configuration.userContentController = controller
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.isOpaque = false
        view.backgroundColor = UIColor(red: 0.06, green: 0.08, blue: 0.07, alpha: 1)
        view.scrollView.backgroundColor = view.backgroundColor
        view.allowsBackForwardNavigationGestures = true
        view.load(URLRequest(url: AppConfiguration.webURL))
        return view
    }()

    func select(_ value: VisionPage) {
        page = value
        guard value != .downloads else { Task { await DownloadManager.shared.refresh() }; return }
        if value == lastWebPage { return }
        let wasVortex = lastWebPage == .vortex
        lastWebPage = value
        if value == .vortex {
            webView.load(URLRequest(url: AppConfiguration.webURL.appendingPathComponent("vortex/")))
        } else if wasVortex {
            var url = AppConfiguration.webURL
            if value == .venture { url = url.appendingPathComponent("venture/") }
            webView.load(URLRequest(url: url))
        } else {
            webView.callAsyncJavaScript("if (window.VisionNativeSelectWorkspace) { window.VisionNativeSelectWorkspace(view); return true; } return false;", arguments: ["view": value.rawValue], in: nil, contentWorld: .page) { [weak self] result in
                if case .success(let switched) = result, switched as? Bool == true { return }
                Task { @MainActor in
                    guard let self else { return }
                    self.webView.load(URLRequest(url: value == .venture ? AppConfiguration.webURL.appendingPathComponent("venture/") : AppConfiguration.webURL))
                }
            }
        }
    }

    func signIn() async {
        guard !authenticating else { return }
        guard bridgeReady else {
            errorMessage = "Vision is still connecting. If this continues, merge the iOS update and refresh the app so the website and FUPCJ Server use the new sign-in bridge."
            return
        }
        authenticating = true
        defer { authenticating = false }
        do {
            let token = try await google.signIn(anchor: webView.window)
            guard AppConfiguration.isTrustedPage(webView.url) else { throw NativeSessionError.invalidResponse }
            _ = try await webView.callAsyncJavaScript("if (!window.VisionNativeReceiveGoogle) throw new Error('Refresh Vision to finish signing in.'); return await window.VisionNativeReceiveGoogle(token);", arguments: ["token": token], in: nil, contentWorld: .page)
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
            // Dismissing Apple's browser leaves the current account unchanged.
        } catch { errorMessage = error.localizedDescription }
    }

    func signOut() async {
        let oldUID = NativeSession.currentUID
        do {
            if bridgeReady {
                _ = try await webView.callAsyncJavaScript("if (window.VisionNativeSignOut) return await window.VisionNativeSignOut(); return true;", arguments: [:], in: nil, contentWorld: .page)
            }
            try NativeSession.clear()
            email = ""
            if let oldUID { await DownloadManager.shared.signOut(uid: oldUID) }
        } catch { errorMessage = error.localizedDescription }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.frameInfo.isMainFrame, message.webView === webView,
              AppConfiguration.isTrustedPage(message.frameInfo.request.url), AppConfiguration.isTrustedPage(webView.url),
              let body = message.body as? [String: Any], let type = body["type"] as? String else {
            replyHandler(nil, "Untrusted Vision message."); return
        }
        switch type {
        case "ready": bridgeReady = true; replyHandler(true, nil)
        case "signIn": replyHandler(true, nil); Task { await signIn() }
        case "downloads": select(.downloads); replyHandler(true, nil)
        case "sessionError": errorMessage = "Your sign-in could not be shared with Vortex. Reconnect in Vision."; replyHandler(true, nil)
        case "session":
            do {
                let oldUID = NativeSession.currentUID
                if body["signedIn"] as? Bool == true {
                    guard let uid = body["uid"] as? String, let token = body["idToken"] as? String,
                          let refresh = body["refreshToken"] as? String, let expires = body["expiresAt"] as? Double,
                          expires.isFinite, expires > Date().timeIntervalSince1970 - 60 else { throw NativeSessionError.invalidResponse }
                    let email = body["email"] as? String ?? ""
                    try NativeSession.save(FirebaseCredentials(uid: uid, email: email, idToken: token, refreshToken: refresh, expiresAt: Date(timeIntervalSince1970: expires)))
                    self.email = email
                    if let oldUID, oldUID != uid { Task { await DownloadManager.shared.signOut(uid: oldUID) } }
                    Task { await DownloadManager.shared.refresh() }
                } else {
                    try NativeSession.clear(); email = ""
                    if let oldUID { Task { await DownloadManager.shared.signOut(uid: oldUID) } }
                }
                replyHandler(true, nil)
            } catch { errorMessage = error.localizedDescription; replyHandler(nil, error.localizedDescription) }
        case "vortexJob":
            guard let id = body["jobID"] as? String, id.range(of: "^[a-f0-9]{24}$", options: .regularExpression) != nil else {
                replyHandler(nil, "Invalid Vortex job."); return
            }
            Task {
                await DownloadManager.shared.adopt(jobID: id)
                if DownloadManager.shared.records.contains(where: { $0.jobID == id && $0.uid == NativeSession.currentUID }) {
                    replyHandler(true, nil)
                } else { replyHandler(nil, DownloadManager.shared.errorMessage ?? "Open Downloads to retry this file.") }
            }
        default: replyHandler(nil, "Unknown Vision message.")
        }
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { loading = true; bridgeReady = false }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { loading = false }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        loading = false
        if (error as NSError).code != NSURLErrorCancelled { errorMessage = "Vision could not connect. Check your internet connection, then refresh." }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        loading = false; errorMessage = "iOS paused the workspace to free memory. Refresh to reconnect to your saved projects."
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if navigationAction.shouldPerformDownload || (url.scheme == "blob" && AppConfiguration.isTrustedPage(webView.url)) { decisionHandler(.download); return }
        if AppConfiguration.isTrustedPage(url) { decisionHandler(.allow); return }
        // Keep untrusted content outside the WebKit view holding the native bridge.
        if navigationAction.navigationType == .linkActivated, ["https", "http", "mailto"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
        decisionHandler(.cancel)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, ["https", "http"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
        return nil
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let name = URL(fileURLWithPath: suggestedFilename).lastPathComponent.replacingOccurrences(of: ":", with: "_")
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("VisionExports", isDirectory: true).appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let target = directory.appendingPathComponent(name.isEmpty ? "Vision-export" : name)
            downloadURLs[ObjectIdentifier(download)] = target
            completionHandler(target)
        } catch { errorMessage = error.localizedDescription; completionHandler(nil) }
    }
    func downloadDidFinish(_ download: WKDownload) { exportURL = downloadURLs.removeValue(forKey: ObjectIdentifier(download)) }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if let url = downloadURLs.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        errorMessage = "The export did not finish. Try saving it again."
    }
}

struct VisionWebView: UIViewRepresentable {
    @ObservedObject var model: VisionWebModel
    func makeUIView(context: Context) -> WKWebView { model.webView }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
