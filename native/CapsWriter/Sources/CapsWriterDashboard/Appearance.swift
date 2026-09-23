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

    /// 只由 Dashboard 右上角的三档 Picker 调用，设置当前 App 的原生外观。
    /// `NSApp.appearance = nil` 是 AppKit 的正式“跟随系统”语义，系统在自动
    /// 切换亮/暗模式时会自行更新窗口，不需要把系统状态复制成第三种 SwiftUI 值。
    func applyToApplication() {
        switch self {
        case .system:
            NSApp.appearance = nil
        case .light:
            NSApp.appearance = NSAppearance(named: .aqua)
        case .dark:
            NSApp.appearance = NSAppearance(named: .darkAqua)
        }
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

/// 窗口保留透明合成基线；顶栏滤镜由 ProgressiveTitlebarBackdrop 安装到窗口 frame 层级。
struct WindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> WindowConfiguratorHostView { WindowConfiguratorHostView() }
    func updateNSView(_ view: WindowConfiguratorHostView, context: Context) { view.configureWindow() }
}

final class WindowConfiguratorHostView: NSView {
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        configureWindow()
    }

    func configureWindow() {
        guard let window else { return }
        // fullSizeContentView 使正文进入标题栏下方，原生 scroll edge 才有可采样的内容。
        window.titlebarAppearsTransparent = true
        window.styleMask.insert(.fullSizeContentView)
        window.isOpaque = false
        window.backgroundColor = .clear
        // 关闭硬分隔而保留系统控件；不再注入失效的 Core Image sibling overlay。
        window.titlebarSeparatorStyle = .none

        // Dashboard 只维护普通窗口模式。macOS 全屏会切换到另一套标题栏、工具栏
        // 和内容布局，与当前透明窗口合成方式不兼容，因此从窗口能力层彻底关闭全屏。
        // 先清掉系统或 SwiftUI 可能预先写入的互斥标志，避免允许与禁止 tiling
        // 同时存在而触发 AppKit 异常；普通缩放、拖动和最小化能力保持不变。
        var behavior = window.collectionBehavior
        behavior.remove(.fullScreenPrimary)
        behavior.remove(.fullScreenAuxiliary)
        behavior.remove(.fullScreenAllowsTiling)
        behavior.remove(.fullScreenNone)
        behavior.remove(.fullScreenDisallowsTiling)
        behavior.insert(.fullScreenNone)
        behavior.insert(.fullScreenDisallowsTiling)
        window.collectionBehavior = behavior
    }
}

/// macOS 15+ 用 SwiftUI 官方 toolbar 默认项控制视觉标题；旧系统只保留原生
/// titleVisibility 回退，不改动 WindowGroup 的语义标题字符串。
struct DashboardWindowTitle: ViewModifier {
    let showTitle: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.toolbar(removing: showTitle ? nil : .title)
        } else {
            content.background(LegacyWindowTitleConfigurator(showTitle: showTitle))
        }
    }
}

/// 删除 NavigationSplitView 自动生成的 sidebar toggle，统一交给业务绑定按钮控制。
struct DashboardSidebarToggleRemoval: ViewModifier {
    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.toolbar(removing: .sidebarToggle)
        } else {
            content
        }
    }
}

private struct LegacyWindowTitleConfigurator: NSViewRepresentable {
    let showTitle: Bool

    func makeNSView(context: Context) -> LegacyWindowTitleView { LegacyWindowTitleView() }

    func updateNSView(_ view: LegacyWindowTitleView, context: Context) {
        view.showTitle = showTitle
        view.configure()
    }
}

private final class LegacyWindowTitleView: NSView {
    var showTitle = false

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        configure()
    }

    func configure() {
        // macOS 13–14 没有 toolbar(removing: .title)；这里只控制系统标题可见性，
        // 保留 WindowGroup 的语义标题和窗口菜单名称。
        window?.titleVisibility = showTitle ? .visible : .hidden
    }
}

/// 业务层按视觉元素命名，AppKit 材质枚举只是底层映射，不能作为组件职责名。
enum DashboardSurfaceStyle {
    /// 用户已确认这款背板观感；系统恰好将它命名为 sidebar，不代表背板属于侧栏。
    static let backdropMaterial: NSVisualEffectView.Material = .sidebar

    /// 顶栏几何配置按视觉元素命名，避免把系统材质枚举名泄漏成业务组件名。
    static let titlebarHeight: CGFloat = 52
    static let titlebarFadeHeight: CGFloat = 24
    // 只控制标题栏主体的最大模糊强度；效果区域高度和渐变距离由上面两个参数负责。
    // 30pt 用于观察强度差异；顶栏高度和底部过渡距离保持不变。
    static let titlebarMaxBlurRadius: CGFloat = 30
}

/// 全窗共享一份磨砂背板，包括侧栏和标题栏下方；辅助功能实色回退由根视图负责。
struct DashboardWindowBackdrop: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView { NSVisualEffectView() }
    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        view.material = DashboardSurfaceStyle.backdropMaterial
        view.blendingMode = .behindWindow
        view.state = .active
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

    /// 隐藏系统工具栏染色背景；自动背景实测出现白色横带，不能用它替代纯模糊。
    @ViewBuilder func hiddenWindowToolbarBackground() -> some View {
        if #available(macOS 15.0, *) {
            toolbarBackgroundVisibility(.hidden, for: .windowToolbar)
        } else {
            self
        }
    }

    /// macOS 15+ 由 SwiftUI scene 层关闭全屏入口；macOS 13–14 继续由
    /// WindowConfigurator 的 AppKit collectionBehavior 提供同一策略。
    @ViewBuilder func disabledWindowFullScreen() -> some View {
        if #available(macOS 15.0, *) {
            windowFullScreenBehavior(.disabled)
        } else {
            self
        }
    }
}
