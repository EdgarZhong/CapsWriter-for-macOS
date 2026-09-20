import Foundation

/// 保持与 ErrorBus 的磁盘格式兼容，不把 Python 内部实现泄漏给后续窗口。
public struct ClientSnapshot: Decodable, Equatable {
    public let state: String
    public let serverConnected: Bool
    public let accessibilityOK: Bool
    public let microphoneOK: Bool
    public let lastHeartbeat: String
    public let lastError: String?
    public let permissionPhase: String?

    private enum CodingKeys: String, CodingKey {
        case state
        case serverConnected = "server_connected"
        case accessibilityOK = "accessibility_ok"
        case microphoneOK = "microphone_ok"
        case lastHeartbeat = "last_heartbeat"
        case lastError = "last_error"
        case permissionPhase = "perm_phase"
    }

    /// 旧客户端写本机无时区 ISO 时间，不能直接当作 UTC 解读。
    public var heartbeatDate: Date? {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: lastHeartbeat) { return date }
        iso.formatOptions = [.withInternetDateTime]
        if let date = iso.date(from: lastHeartbeat) { return date }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.calendar = Calendar(identifier: .gregorian)
        local.timeZone = .current
        local.isLenient = false
        local.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return local.date(from: lastHeartbeat)
    }
}

/// 状态过期与未运行分开呈现，避免残留文件让 Dashboard 假报在线。
public enum ClientAvailability: Equatable {
    case stopped, unreadable, stale, starting, connecting, ready, recording, error
}

public struct SnapshotResult: Equatable {
    public let availability: ClientAvailability
    public let snapshot: ClientSnapshot?
}

/// 只读兼容适配器；URL 与时间可注入，测试不依赖用户真实服务。
public struct SnapshotReader {
    public init() {}

    public static var defaultURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".capswriter/state/status.json")
    }

    public func read(at url: URL = Self.defaultURL, now: Date = Date()) -> SnapshotResult {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch let error as NSError {
            // 只有文件确实不存在才报告未运行；权限或 I/O 故障必须可见。
            let missing = error.domain == NSCocoaErrorDomain
                && error.code == NSFileReadNoSuchFileError
            return SnapshotResult(availability: missing ? .stopped : .unreadable, snapshot: nil)
        }
        return decode(data, now: now)
    }

    public func decode(_ data: Data, now: Date) -> SnapshotResult {
        guard let snapshot = try? JSONDecoder().decode(ClientSnapshot.self, from: data) else {
            return SnapshotResult(availability: .unreadable, snapshot: nil)
        }
        guard let heartbeat = snapshot.heartbeatDate,
              (0...10).contains(now.timeIntervalSince(heartbeat)) else {
            return SnapshotResult(availability: .stale, snapshot: snapshot)
        }
        let availability: ClientAvailability
        switch snapshot.state {
        case "starting": availability = .starting
        case "connecting": availability = .connecting
        case "ready": availability = snapshot.serverConnected ? .ready : .connecting
        case "recording": availability = snapshot.serverConnected ? .recording : .connecting
        default: availability = .error
        }
        return SnapshotResult(availability: availability, snapshot: snapshot)
    }
}
