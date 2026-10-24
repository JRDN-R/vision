import SwiftUI
import WebKit
import UniformTypeIdentifiers
import AuthenticationServices

@MainActor
final class VisionWebModel: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply, WKDownloadDelegate {
    @Published var loading = true
    @Published var loadFailure: String?
    @Published var errorMessage: String?
    @Published var email = (try? NativeSession.load())?.email ?? ""
    @Published var exportURL: URL?
    @Published var bridgeReady = false
    @Published var showingDownloads = false
    @Published var showingSettings = false
    private let google = GoogleSignIn()
    private var authenticating = false
    private var downloadURLs: [ObjectIdentifier: URL] = [:]
    private var started = false
    private var activeNavigation: WKNavigation?
    private var loadWatchdog: Task<Void, Never>?

    lazy var webView: WKWebView = {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        let controller = WKUserContentController()
        controller.addScriptMessageHandler(self, contentWorld: .page, name: "vision")
        controller.addUserScript(WKUserScript(source: WorkspaceScripts.bridge, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        controller.addUserScript(WKUserScript(source: WorkspaceScripts.sidebar, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        configuration.userContentController = controller
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.isOpaque = false
        view.backgroundColor = UIColor(red: 0.06, green: 0.08, blue: 0.07, alpha: 1)
        view.scrollView.backgroundColor = view.backgroundColor
        view.scrollView.contentInsetAdjustmentBehavior = .never
        view.allowsBackForwardNavigationGestures = true
        // Do not start navigation inside a lazy initializer: delegates and the
        // native bridge must observe the fully initialized, mounted web view.
        return view
    }()

    func start() {
        guard !started else { return }
        started = true
        navigate(to: AppConfiguration.workspaceURL)
    }

    func reloadWorkspace() {
        let current = webView.url
        navigate(to: AppConfiguration.isTrustedPage(current) ? current! : AppConfiguration.workspaceURL)
    }

    func openWebsite() { UIApplication.shared.open(AppConfiguration.workspaceURL) }

    private func navigate(to url: URL) {
        guard AppConfiguration.isTrustedPage(url) else {
            failLoading("The workspace address is not an approved Vision page. Reload the workspace to return to Vision.")
            return
        }
        beginLoading()
        activeNavigation = webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 60))
    }

    private func beginLoading() {
        loadWatchdog?.cancel()
        loading = true
        bridgeReady = false
        loadFailure = nil
        loadWatchdog = Task { [weak self] in
            do { try await Task.sleep(nanoseconds: 60_000_000_000) } catch { return }
            guard !Task.isCancelled, let self, self.loading else { return }
            self.failLoading("Vision did not finish loading within 60 seconds. Check the connection, then retry. Your saved account and projects have not been cleared.")
            self.webView.stopLoading()
        }
    }

    private func finishLoading() {
        loadWatchdog?.cancel()
        loadWatchdog = nil
        loading = false
    }

    private func failLoading(_ message: String) {
        finishLoading()
        bridgeReady = false
        loadFailure = message
    }

    func openDownloads() {
        showingDownloads = true
        Task { await DownloadManager.shared.refresh() }
    }

    func signIn() async {
        guard !authenticating else { return }
        guard bridgeReady else {
            errorMessage = loading
                ? "The workspace is still loading. Sign in once it appears. A failed connection will show a Retry button."
                : "The workspace loaded, but its sign-in bridge is not ready. Use Reload workspace and try again."
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
        case "ready": bridgeReady = true; loadFailure = nil; finishLoading(); replyHandler(true, nil)
        case "signIn": replyHandler(true, nil); Task { await signIn() }
        case "downloads": openDownloads(); replyHandler(true, nil)
        case "settings": showingSettings = true; replyHandler(true, nil)
        case "reload": replyHandler(true, nil); Task { reloadWorkspace() }
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

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        activeNavigation = navigation
        beginLoading()
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard navigation === activeNavigation else { return }
        finishLoading()
        loadFailure = nil
        guard AppConfiguration.isTrustedPage(webView.url) else { return }
        // Also covers a workspace installed by the site's document.write loader.
        webView.evaluateJavaScript(WorkspaceScripts.sidebar, completionHandler: nil)
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(navigation, error: error)
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        navigationFailed(navigation, error: error)
    }
    private func navigationFailed(_ navigation: WKNavigation?, error: Error) {
        guard navigation === activeNavigation else { return }
        let value = error as NSError
        if value.domain == NSURLErrorDomain && value.code == NSURLErrorCancelled {
            // Superseded navigation and explicit Stop aren't network failures.
            // Keep a more useful HTTP/policy/timeout error already recorded.
            finishLoading()
            return
        }
        failLoading("Vision could not load the workspace (\(value.domain), \(value.code)). Check the connection and retry. Your saved data has not been cleared.")
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        failLoading("iOS stopped the workspace to free memory. Reload to reconnect to your saved projects.")
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
        guard let url = navigationAction.request.url else {
            failLoading("The workspace tried to open an invalid address. Reload Vision to retry.")
            decisionHandler(.cancel); return
        }
        if (navigationAction.shouldPerformDownload || url.scheme == "blob") && AppConfiguration.isTrustedPage(webView.url) {
            decisionHandler(.download); return
        }
        if AppConfiguration.isTrustedPage(url) { decisionHandler(.allow); return }
        // Keep untrusted content outside the WebKit view holding the native bridge.
        if navigationAction.navigationType == .linkActivated, ["https", "http", "mailto"].contains(url.scheme ?? "") {
            UIApplication.shared.open(url)
        } else if loading {
            failLoading("Vision blocked navigation outside its approved workspace. Reload to return to the original Vision page.")
        }
        decisionHandler(.cancel)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if navigationResponse.isForMainFrame, let response = navigationResponse.response as? HTTPURLResponse, response.statusCode >= 400 {
            failLoading("The Vision website returned HTTP \(response.statusCode). Retry or open the website to check its availability.")
            decisionHandler(.cancel); return
        }
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            if AppConfiguration.isTrustedPage(url) { navigate(to: url) }
            else if ["https", "http"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
        }
        return nil
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { finishLoading(); download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { finishLoading(); download.delegate = self }
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
