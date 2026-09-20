import AppKit
import SwiftUI

/// 外观仅保存在当前 App 域中，不改动系统设置或现有听写客户端。
enum DashboardAppearance: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }
    var title: String {
        switch self { case .system: return "跟随系统"; case .light: return "浅色"; case .dark: return "深色" }
    }
    var symbol: String {
        switch self { case .system: return "circle.lefthalf.filled"; case .light: return "sun.max"; case .dark: return "moon" }
    }
    var scheme: ColorScheme? {
        switch self { case .system: return nil; case .light: return .light; case .dark: return .dark }
    }
}

/// 由系统负责采样壁纸与混合；减少透明度时使用不透明背景。
struct SidebarMaterial: NSViewRepresentable {
    var opaque: Bool
    func makeNSView(context: Context) -> NSVisualEffectView { NSVisualEffectView() }
    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        view.material = opaque ? .windowBackground : .sidebar
        view.blendingMode = .behindWindow
        view.state = .followsWindowActiveState
    }
}

/// 玻璃只用于小型操作控件，正文保持稳定表面，避免层层透叠。
struct ControlGlass: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    func body(content: Content) -> some View {
        if reduceTransparency {
            content.background(Color(nsColor: .controlBackgroundColor), in: Capsule())
        } else if #available(macOS 26.0, *) {
            content.glassEffect(.regular, in: Capsule())
        } else {
            content.background(.regularMaterial, in: Capsule())
        }
    }
}

/// 图标读取打包素材，缺失时退回应用图标，不依赖开发者机器路径。
struct BrandIcon: View {
    var size: CGFloat
    private var icon: NSImage {
        if let url = Bundle.main.url(forResource: "app-icon", withExtension: "png"),
           let image = NSImage(contentsOf: url) { return image }
        return NSApplication.shared.applicationIconImage
    }
    var body: some View {
        Image(nsImage: icon).resizable().interpolation(.high)
            .frame(width: size, height: size).accessibilityHidden(true)
    }
}

/// 整窗材质分层（从下到上，恰好三层，口径见 docs/dashboard-窗口材质分层规格.md）：
/// 1. 背板：DashboardGlassBackground 位于 SwiftUI 根 ZStack 最底层，铺满整窗；
/// 2. 标题栏：系统层，红绿灯、窗口标题与外观切换都在这一层里，不再叠加自绘渐变带；
///    滚动渐隐由系统 scrollEdgeEffectStyle 完成（见 DashboardView），不自绘第二层；
/// 3. 侧栏：macOS 26 用 NavigationSplitView 原生玻璃，不再叠加自定义材质；
///    13–25 回退用 SidebarMaterial，允许比 26 略实。
///
/// 架构口径：NSWindow 是透明合成容器（isOpaque = false + clear 背景），它只负责
/// 「允许透出」；真正的磨砂由 SwiftUI 根层级里的 NSVisualEffectView(.popover,
/// .behindWindow) 提供。此前把材质插到 window.contentView 最底层的做法会被
/// SwiftUI 各容器自身的不透明背景完整遮住，永不透光，已废弃。

/// 只配置窗口，不创建任何视觉效果视图。窗口配置必须在 viewDidMoveToWindow 里做：
/// 异步等 window 的做法会静默失败，配置根本没生效。
struct WindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> WindowConfiguratorHostView { WindowConfiguratorHostView() }
    func updateNSView(_ view: WindowConfiguratorHostView, context: Context) {}
}

final class WindowConfiguratorHostView: NSView {
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window else { return }
        // 顶栏并入内容区：滚动内容才能延伸到标题栏下方，渐隐层才有东西可盖。
        window.titlebarAppearsTransparent = true
        window.styleMask.insert(.fullSizeContentView)
        // 透明窗口 + 根层磨砂材质 + 各级容器背景清空，三者缺一不可：
        // 只开透明而不清容器背景，就是之前标题栏白色残块的成因。
        window.isOpaque = false
        window.backgroundColor = .clear
        // 系统窗口圆角与阴影保持默认，不自行绘制窗口 mask。
    }
}

/// 真正的窗口磨砂背板：必须是 SwiftUI hierarchy 根 ZStack 的最底层，
/// 而不是 AppKit hosting view 外部的 sibling/backdrop。
/// 材质固定 .popover + .behindWindow + .active，本轮不再做材质选型实验。
struct DashboardGlassBackground: NSViewRepresentable {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    func makeNSView(context: Context) -> NSVisualEffectView { NSVisualEffectView() }
    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        // 减少透明度时退回窗口内实色填充，保证文字对比度。
        view.material = reduceTransparency ? .windowBackground : .popover
        view.blendingMode = reduceTransparency ? .withinWindow : .behindWindow
        view.state = .active
    }
}

/// 滚动渐隐（第二层职责）：macOS 26 起系统对滚到标题栏/工具栏下方的内容自动做柔和
/// 模糊渐隐（WWDC25 Session 323），只需声明 .soft；不要自绘渐变层再造第二条厚带。
/// 13–25 无此 API，回退为无渐隐的单层滚动区（规格允许的降级）。
struct SoftScrollEdge: ViewModifier {
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *) {
            content.scrollEdgeEffectStyle(.soft, for: .top)
        } else {
            content
        }
    }
}

/// 内容面压在整窗背板之上：适度透光让壁纸颜色渗入卡片，层次轻盈而不是一块实灰。
struct ContentSurface: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    func body(content: Content) -> some View {
        // 减少透明度时恢复近不透明填充，保证文字对比度。
        let fillOpacity = reduceTransparency ? 0.94 : (scheme == .dark ? 0.42 : 0.58)
        content.background {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(Color(nsColor: .controlBackgroundColor).opacity(fillOpacity))
                .overlay {
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .strokeBorder(.primary.opacity(scheme == .dark ? 0.08 : 0.055), lineWidth: 1)
                }
                .shadow(color: .black.opacity(scheme == .dark ? 0.12 : 0.025), radius: 16, y: 6)
        }
    }
}

extension View {
    /// 清空 window 容器背景（macOS 15+ 才有该 placement；更低版本保留系统默认）。
    /// 透明窗口 + 根层磨砂的架构要求 window 容器背景透明，否则系统默认背景会把
    /// 根层材质遮住。macOS 上 containerBackground 没有 navigation/navigationSplitView
    /// placement（仅 iOS 系可用），分列背景由窗口透明 + 各列自身背景设置承担。
    @ViewBuilder func clearWindowContainerBackground() -> some View {
        if #available(macOS 15.0, *) {
            containerBackground(.clear, for: .window)
        } else {
            self
        }
    }

    /// 隐藏系统 window toolbar 自带背景，避免工具栏再铺一层独立实色（macOS 15+）。
    @ViewBuilder func hiddenWindowToolbarBackground() -> some View {
        if #available(macOS 15.0, *) {
            toolbarBackgroundVisibility(.hidden, for: .windowToolbar)
        } else {
            self
        }
    }
}
