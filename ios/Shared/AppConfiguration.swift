import Foundation

enum AppConfiguration {
    static let serverURL = configuredURL("VisionServerURL", fallback: "https://desktop-vjt2br2.tail385c9d.ts.net")
    static let webURL = configuredURL("VisionWebURL", fallback: "https://jrdn-r.github.io/vision/")
    // Open the actual workspace, not the index.html document.write bootstrap.
    static var workspaceURL: URL { webURL.appendingPathComponent("Vision.html") }
    static let firebaseAPIKey = "AIzaSyDh1AHhi41cAXcSFnvFkfeZWmxc8gI0zSg" // Public Firebase web configuration, not an account secret.
    static var appGroupIdentifier: String { requiredString("VisionAppGroup") }
    static var keychainAccessGroup: String { requiredString("VisionKeychainGroup") }
    static var pushEnabled: Bool { Bundle.main.object(forInfoDictionaryKey: "VisionPushEnabled") as? Bool ?? false }
    static let callbackScheme = "visioncompanion"

    static func endpoint(_ path: String) -> URL {
        precondition(path.hasPrefix("/api/") && !path.contains(".."))
        return URL(string: path, relativeTo: serverURL)!.absoluteURL
    }

    static func isTrustedPage(_ url: URL?) -> Bool {
        guard let url, let candidate = URLComponents(url: url, resolvingAgainstBaseURL: true),
              let origin = URLComponents(url: webURL, resolvingAgainstBaseURL: true),
              candidate.scheme?.lowercased() == "https",
              candidate.host?.lowercased() == origin.host?.lowercased(),
              (candidate.port ?? 443) == (origin.port ?? 443),
              candidate.user == nil, candidate.password == nil else { return false }
        // URL.path drops a directory's trailing slash. Comparing it with an
        // allowlist containing /vision/ silently blocked our own initial load.
        // URLComponents preserves the encoded path; explicitly allow directory
        // and index forms without trusting arbitrary same-origin pages.
        let root = origin.percentEncodedPath.hasSuffix("/")
            ? String(origin.percentEncodedPath.dropLast()) : origin.percentEncodedPath
        let routes = ["", "/", "/index.html", "/Vision.html", "/venture", "/venture/",
                      "/venture/index.html", "/vortex", "/vortex/", "/vortex/index.html"]
        return routes.contains { candidate.percentEncodedPath == root + $0 }
    }

    private static func configuredURL(_ key: String, fallback: String) -> URL {
        let raw = Bundle.main.object(forInfoDictionaryKey: key) as? String ?? fallback
        guard let value = URL(string: raw), value.scheme == "https", value.host != nil,
              value.user == nil, value.password == nil, value.query == nil, value.fragment == nil else {
            preconditionFailure("Invalid HTTPS address in " + key)
        }
        return value
    }
    private static func requiredString(_ key: String) -> String {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String,
              !value.isEmpty, !value.contains("$(") else { preconditionFailure("Missing signing configuration: " + key) }
        return value
    }
}
