import Foundation

/// 处理入口由视图执行；状态层不直接打开系统设置或启动服务。
public enum ReadinessAction: String {
    case accessibility, inputMonitoring, microphone, inference, settings, refresh
}

public struct ReadinessIssue: Identifiable, Equatable {
    public let title: String
    public let detail: String
    public let symbol: String
    public let action: ReadinessAction
    public var id: String { action.rawValue }
}

/// 从新鲜快照聚合就绪条件，禁止用单一 ready 字段掩盖权限缺失。
public struct Readiness {
    public let title: String
    public let detail: String
    public let symbol: String
    public let issues: [ReadinessIssue]
    public let ready: Bool

    public init(_ result: SnapshotResult) {
        var items: [ReadinessIssue] = []
        switch result.availability {
        case .stopped:
            items.append(.init(title: "客户端尚未启动", detail: "启动客户端后，这里会自动更新运行状态。", symbol: "power", action: .settings))
        case .unreadable:
            items.append(.init(title: "无法读取客户端状态", detail: "检查客户端是否正在运行，或重新刷新状态。", symbol: "questionmark.circle", action: .refresh))
        case .stale:
            items.append(.init(title: "客户端状态已过期", detail: "超过 10 秒未收到心跳，请检查客户端。", symbol: "clock.badge.exclamationmark", action: .settings))
        default:
            if let snapshot = result.snapshot {
                if !snapshot.accessibilityOK || snapshot.permissionPhase == "guide_ax" || snapshot.permissionPhase == "revoked" {
                    items.append(.init(title: "需要辅助功能权限", detail: "允许 CapsWriter 处理快捷键并输入文字。", symbol: "hand.point.up.left", action: .accessibility))
                }
                if snapshot.permissionPhase == "guide_im" {
                    items.append(.init(title: "需要输入监控权限", detail: "允许 CapsWriter 识别 Caps Lock 按键。", symbol: "keyboard", action: .inputMonitoring))
                }
                if !snapshot.microphoneOK {
                    items.append(.init(title: "麦克风尚未就绪", detail: "检查麦克风授权；设备打开失败也可能导致此状态。", symbol: "mic.slash", action: .microphone))
                }
                if !snapshot.serverConnected {
                    items.append(.init(title: "识别服务未连接", detail: "检查推理资源与服务运行情况。", symbol: "network.slash", action: .inference))
                }
                if items.isEmpty && result.availability == .error {
                    items.append(.init(title: "客户端需要处理", detail: snapshot.lastError ?? "请检查客户端运行状态。", symbol: "exclamationmark.triangle", action: .settings))
                }
                if items.isEmpty && snapshot.permissionPhase != nil && snapshot.permissionPhase != "ready" {
                    items.append(.init(title: "正在检查输入权限", detail: "等待客户端完成权限与快捷键检测。", symbol: "keyboard", action: .settings))
                }
            }
        }
        issues = items
        ready = items.isEmpty && result.availability == .ready
        if let issue = items.first {
            title = issue.title; detail = issue.detail; symbol = issue.symbol
        } else if result.availability == .recording {
            title = "正在聆听"; detail = "松开 Caps Lock 后完成识别。"; symbol = "waveform"
        } else if ready {
            title = "准备好听你说话"; detail = "按住 Caps Lock 说话，松手后输入。"; symbol = "checkmark.circle"
        } else {
            title = "正在准备"; detail = "客户端完成初始化后即可开始听写。"; symbol = "arrow.trianglehead.2.clockwise.rotate.90"
        }
    }
}
