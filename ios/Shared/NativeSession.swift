import Foundation
import Security
import Darwin

struct FirebaseCredentials: Codable {
    let uid: String
    let email: String
    let idToken: String
    let refreshToken: String
    let expiresAt: Date
}

enum NativeSessionError: LocalizedError {
    case signedOut, invalidResponse, accountChanged, keychain(OSStatus), server(String)
    var errorDescription: String? {
        switch self {
        case .signedOut: return "Open Vision and sign in before using Vortex."
        case .invalidResponse: return "Your sign-in could not be renewed. Open Vision and sign in again."
        case .accountChanged: return "The signed-in account changed. Try again."
        case .keychain: return "Vision could not access its shared sign-in. Check that all targets use the same signing team and Keychain group."
        case .server(let message): return message
        }
    }
}

enum NativeSession {
    private static let lock = NSRecursiveLock()
    private static var lockDepth = 0
    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "VisionFirebaseSession.v1",
         kSecAttrAccount as String: "current",
         kSecAttrAccessGroup as String: AppConfiguration.keychainAccessGroup]
    }
    static var currentUID: String? { (try? load())?.uid }

    static func load() throws -> FirebaseCredentials? {
        try locked {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw NativeSessionError.keychain(status) }
        guard let data = result as? Data else { throw NativeSessionError.invalidResponse }
        return try JSONDecoder().decode(FirebaseCredentials.self, from: data)
        }
    }

    static func save(_ value: FirebaseCredentials) throws {
        try locked {
        guard !value.uid.isEmpty, value.uid.count <= 128, !value.idToken.isEmpty,
              value.idToken.count < 20000, !value.refreshToken.isEmpty, value.refreshToken.count < 20000 else {
            throw NativeSessionError.invalidResponse
        }
        let attributes: [String: Any] = [kSecValueData as String: try JSONEncoder().encode(value),
                                         kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw NativeSessionError.keychain(status) }
        }
    }

    static func clear() throws {
        try locked {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw NativeSessionError.keychain(status) }
        }
    }

    static func validIDToken() async throws -> String {
        guard let saved = try load() else { throw NativeSessionError.signedOut }
        if saved.expiresAt.timeIntervalSinceNow > 120 { return saved.idToken }
        var request = URLRequest(url: URL(string: "https://securetoken.googleapis.com/v1/token?key=" + AppConfiguration.firebaseAPIKey)!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 25
        var form = URLComponents()
        form.queryItems = [URLQueryItem(name: "grant_type", value: "refresh_token"), URLQueryItem(name: "refresh_token", value: saved.refreshToken)]
        request.httpBody = form.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B").data(using: .utf8)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let idToken = result["id_token"] as? String, let refresh = result["refresh_token"] as? String,
              result["user_id"] as? String == saved.uid else { throw NativeSessionError.invalidResponse }
        let expiry = Double(result["expires_in"] as? String ?? "3600") ?? 3600
        let value = FirebaseCredentials(uid: saved.uid, email: saved.email, idToken: idToken,
                                        refreshToken: refresh, expiresAt: Date().addingTimeInterval(expiry))
        try saveRefreshed(value, replacing: saved)
        return idToken
    }

    private static func saveRefreshed(_ new: FirebaseCredentials, replacing old: FirebaseCredentials) throws {
        try locked {
        guard let current = try load(), current.uid == old.uid else { throw NativeSessionError.accountChanged }
        // A newer sign-in/refresh wins if another app process already stored it.
        if current.refreshToken == old.refreshToken { try save(new) }
        }
    }

    // The app and extension are separate processes. Hold the same file lock
    // around Keychain read/compare/write so a late refresh cannot undo sign-out.
    private static func locked<T>(_ body: () throws -> T) throws -> T {
        lock.lock(); defer { lock.unlock() }
        if lockDepth > 0 { return try body() }
        guard let directory = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: AppConfiguration.appGroupIdentifier) else {
            throw NativeSessionError.server("Vision's shared storage is unavailable. Check App Groups signing in Xcode.")
        }
        let path = directory.appendingPathComponent("VisionSession.lock").path
        let descriptor = open(path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw NativeSessionError.keychain(errSecIO) }
        defer { close(descriptor) }
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: path)
        guard flock(descriptor, LOCK_EX) == 0 else { throw NativeSessionError.keychain(errSecIO) }
        defer { flock(descriptor, LOCK_UN) }
        lockDepth = 1
        defer { lockDepth = 0 }
        return try body()
    }
}
