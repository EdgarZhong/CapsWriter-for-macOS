import Foundation
import DashboardCore

// 命令行工具链不附带 XCTest，断言失败直接返回非零退出码。
func XCTAssertEqual<T: Equatable>(_ actual: T, _ expected: T,
                                  file: StaticString = #file, line: UInt = #line) {
    guard actual == expected else { fatalError("断言失败：\(actual) != \(expected)", file: file, line: line) }
}
final class ClientSnapshotTests {
    // 使用固定带时区时刻，保证测试不受执行机器当前日期影响。
    private let now = ISO8601DateFormatter().date(from: "2026-09-20T10:00:00Z")!
    private let reader = SnapshotReader()

    private func fixture(state: String = "ready", connected: Bool = true,
                         heartbeat: String = "2026-09-20T10:00:00Z") throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "state": state, "server_connected": connected,
            "accessibility_ok": true, "microphone_ok": false,
            "last_heartbeat": heartbeat, "last_error": NSNull()
        ])
    }

    func testCurrentPythonLocalTimestamp() throws {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        let result = reader.decode(try fixture(heartbeat: formatter.string(from: now)), now: now)
        XCTAssertEqual(result.availability, .ready)
        // 麦克风未标记授权不能被状态适配器伪造为已授权。
        XCTAssertEqual(result.snapshot?.microphoneOK, false)
    }

    func testFreshnessBoundaryAndFutureTimestamp() throws {
        for (timestamp, expected) in [
            ("2026-09-20T09:59:50Z", ClientAvailability.ready),
            ("2026-09-20T09:59:49Z", .stale),
            ("2026-09-20T10:00:01Z", .stale),
            ("invalid", .stale)
        ] {
            XCTAssertEqual(reader.decode(try fixture(heartbeat: timestamp), now: now).availability, expected)
        }
    }

    func testConnectionAndStateMapping() throws {
        for (state, connected, expected) in [
            ("ready", false, ClientAvailability.connecting),
            ("recording", true, .recording), ("recording", false, .connecting),
            ("starting", false, .starting), ("connecting", false, .connecting),
            ("error", true, .error), ("future_state", true, .error)
        ] {
            XCTAssertEqual(reader.decode(try fixture(state: state, connected: connected), now: now).availability, expected)
        }
    }

    func testMalformedAndIncompleteSnapshots() {
        for value in ["{", "{}", "null"] {
            XCTAssertEqual(reader.decode(Data(value.utf8), now: now).availability, .unreadable)
        }
    }

    func testMissingFileIsStopped() {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        XCTAssertEqual(reader.read(at: path, now: now).availability, .stopped)
    }
}

let checks = ClientSnapshotTests()
try checks.testCurrentPythonLocalTimestamp()
try checks.testFreshnessBoundaryAndFutureTimestamp()
try checks.testConnectionAndStateMapping()
checks.testMalformedAndIncompleteSnapshots()
checks.testMissingFileIsStopped()
print("通过：5 组状态兼容检查。")

// 逐项覆盖概览处理入口，不触碰系统权限或用户快照。
func checkReadiness(state: String = "ready", connected: Bool = true, ax: Bool = true,
                    mic: Bool = true, phase: String = "ready") throws -> Readiness {
    let data = try JSONSerialization.data(withJSONObject: [
        "state": state, "server_connected": connected, "accessibility_ok": ax,
        "microphone_ok": mic, "perm_phase": phase, "last_heartbeat": "2026-09-20T10:00:00Z"
    ])
    let now = ISO8601DateFormatter().date(from: "2026-09-20T10:00:00Z")!
    return Readiness(SnapshotReader().decode(data, now: now))
}
XCTAssertEqual(try checkReadiness().ready, true)
XCTAssertEqual(try checkReadiness(ax: false).issues.first?.action, .accessibility)
XCTAssertEqual(try checkReadiness(mic: false).issues.first?.action, .microphone)
XCTAssertEqual(try checkReadiness(phase: "guide_im").issues.first?.action, .inputMonitoring)
XCTAssertEqual(try checkReadiness(connected: false).issues.first?.action, .inference)
XCTAssertEqual(try checkReadiness(state: "error").issues.first?.action, .settings)
XCTAssertEqual(try checkReadiness(ax: false, mic: false).ready, false)
XCTAssertEqual(try checkReadiness(ax: false, mic: false).issues.count, 2)
XCTAssertEqual(try checkReadiness(state: "recording").title, "正在聆听")
print("通过：9 项就绪条件与处理入口检查。")
