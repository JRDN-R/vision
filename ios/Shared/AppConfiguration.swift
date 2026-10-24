import Foundation

enum AppConfiguration {
    static let serverURL = configuredURL("VisionServerURL", fallback: "https://desktop-vjt2br2.tail385c9d.ts.net")
    static let webURL = configuredURL("VisionWebURL", fallback: "https://jrdn-r.github.io/vision/")
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
        guard let url, url.scheme == "https", url.host == webURL.host, url.port == webURL.port,
              url.user == nil, url.password == nil else { return false }
        let root = webURL.path.hasSuffix("/") ? webURL.path : webURL.path + "/"
        return [root, root + "index.html", root + "Vision.html", root + "venture/",
                root + "venture/index.html", root + "vortex/", root + "vortex/index.html"].contains(url.path)
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
