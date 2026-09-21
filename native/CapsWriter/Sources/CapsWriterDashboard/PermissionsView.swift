import AppKit
import SwiftUI

/// 授权属于客户端启动引导，独立于持久化听写/识别设置。
struct PermissionsView: View {
    @StateObject private var resources = ResourceStore()
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 14) {
                BrandIcon(size: 48)
                VStack(alignment: .leading, spacing: 6) {
                    Text("允许 CapsWriter 为你输入").font(.title2.weight(.semibold))
                    Text("请为 CapsWriter 客户端开启以下权限。")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            VStack(spacing: 0) {
                row("辅助功能", detail: "处理快捷键并输入文字", icon: "hand.point.up.left", pane: "Privacy_Accessibility")
                Divider()
                row("输入监控", detail: "识别 Caps Lock 按键", icon: "keyboard", pane: "Privacy_ListenEvent")
                Divider()
                row("麦克风", detail: "录制语音用于本地识别", icon: "mic", pane: "Privacy_Microphone")
            }
            Text("授权对象是 CapsWriter 客户端，不是此开发窗口。修改权限后，按系统提示重新启动客户端。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let message = error ?? resources.error {
                Text(message).font(.caption).foregroundStyle(.orange)
            }
            HStack {
                Spacer()
                Button("启动客户端") { resources.startClient() }.disabled(resources.busy)
            }
        }.padding(28).frame(width: 500)
    }
    private func row(_ title: String, detail: String, icon: String, pane: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon).frame(width: 24).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.body.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button("前往授权") {
                // 仅导航系统设置，不自动授予权限或弹出 Dashboard 的权限请求。
                if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)"),
                   !NSWorkspace.shared.open(url) { error = "无法打开系统设置，请手动进入隐私与安全性。" }
            }
        }.padding(.vertical, 16)
    }
}
