import AuthenticationServices
import CryptoKit
import Foundation
import Security
import UIKit

@MainActor
final class GoogleSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?
    private var anchor: UIWindow?

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        anchor ?? UIWindow()
    }

    func signIn(anchor: UIWindow?) async throws -> String {
        guard session == nil else { throw NativeSessionError.server("A sign-in window is already open.") }
        self.anchor = anchor
        let verifier = try randomToken()
        let state = try randomToken()
        let challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
        let started = try await post("/api/ios/auth/start", body: ["challenge": challenge, "state": state])
        guard let id = started["id"] as? String, let raw = started["authURL"] as? String,
              let url = URL(string: raw), url.scheme == "https", url.host == AppConfiguration.webURL.host,
              url.path == AppConfiguration.webURL.appendingPathComponent("ios-signin.html").path else {
            throw NativeSessionError.invalidResponse
        }
        let callback: URL = try await withCheckedThrowingContinuation { continuation in
            let authentication = ASWebAuthenticationSession(url: url, callbackURLScheme: AppConfiguration.callbackScheme) { url, error in
                Task { @MainActor in
                    self.session = nil
                    if let error { continuation.resume(throwing: error) }
                    else if let url { continuation.resume(returning: url) }
                    else { continuation.resume(throwing: NativeSessionError.invalidResponse) }
                }
            }
            authentication.presentationContextProvider = self
            authentication.prefersEphemeralWebBrowserSession = false
            self.session = authentication
            if !authentication.start() {
                self.session = nil
                continuation.resume(throwing: NativeSessionError.server("Apple's sign-in window could not open. Try again."))
            }
        }
        guard callback.scheme == AppConfiguration.callbackScheme, callback.host == "auth",
              let parts = URLComponents(url: callback, resolvingAgainstBaseURL: false) else { throw NativeSessionError.invalidResponse }
        let values = Dictionary((parts.queryItems ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { _, _ in "" })
        guard values["state"] == state, values["id"] == id, let code = values["code"], !code.isEmpty else {
            throw NativeSessionError.invalidResponse
        }
        let result = try await post("/api/ios/auth/exchange", body: ["id": id, "state": state, "code": code, "verifier": verifier])
        guard let token = result["googleAccessToken"] as? String, !token.isEmpty, token.count < 20000 else {
            throw NativeSessionError.invalidResponse
        }
        return token
    }

    private func randomToken() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { throw NativeSessionError.invalidResponse }
        return Data(bytes).base64URL
    }
    private func post(_ path: String, body: [String: String]) async throws -> [String: Any] {
        var request = URLRequest(url: AppConfiguration.endpoint(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 25
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw NativeSessionError.invalidResponse }
        let result = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        guard (200..<300).contains(response.statusCode) else {
            throw NativeSessionError.server(result["error"] as? String ?? (response.statusCode == 404 ? "Update FUPCJ Server before connecting the iOS app." : "FUPCJ Server could not complete sign-in."))
        }
        return result
    }
}

private extension Data {
    var base64URL: String { base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
}
