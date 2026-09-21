import AppKit
import CoreImage
import CoreImage.CIFilterBuiltins
import SwiftUI

/// 将顶栏渐进模糊附着到窗口真实的 AppKit 层级。
///
/// 这个代表视图本身不绘制任何材质，只作为 SwiftUI 生命周期钩子；真正的滤镜视图
/// 会被安装到 contentView 的父视图中，位于 SwiftUI 内容之上、系统标题栏控件之下。
struct ProgressiveTitlebarBackdrop: NSViewRepresentable {
    let titlebarHeight: CGFloat
    let fadeHeight: CGFloat
    let maxBlurRadius: CGFloat
    let isEnabled: Bool

    func makeNSView(context: Context) -> ProgressiveTitlebarAttachmentView {
        let view = ProgressiveTitlebarAttachmentView()
        view.titlebarHeight = titlebarHeight
        view.fadeHeight = fadeHeight
        view.maxBlurRadius = maxBlurRadius
        view.isEnabled = isEnabled
        return view
    }

    func updateNSView(
        _ nsView: ProgressiveTitlebarAttachmentView,
        context: Context
    ) {
        nsView.titlebarHeight = titlebarHeight
        nsView.fadeHeight = fadeHeight
        nsView.maxBlurRadius = maxBlurRadius
        nsView.isEnabled = isEnabled
        nsView.installIfPossible()
    }

    static func dismantleNSView(
        _ nsView: ProgressiveTitlebarAttachmentView,
        coordinator: ()
    ) {
        nsView.uninstall()
    }
}

/// SwiftUI 代表视图的生命周期桥接层。
///
/// 它故意不在自身所在的 SwiftUI hierarchy 中显示滤镜。SwiftUI 初始化窗口时可能会
/// 多次替换 hosting root，因此安装操作会推迟一个 run-loop，并在窗口层级变化时重试。
final class ProgressiveTitlebarAttachmentView: NSView {
    var titlebarHeight: CGFloat = 52 {
        didSet { updateBackdropGeometry() }
    }

    var fadeHeight: CGFloat = 24 {
        didSet { updateBackdropGeometry() }
    }

    var maxBlurRadius: CGFloat = 16 {
        didSet { updateBackdropGeometry() }
    }

    var isEnabled: Bool = true {
        didSet { updateBackdropGeometry() }
    }

    private let backdropView = ProgressiveTitlebarBlurView()
    private weak var hostView: NSView?
    private weak var contentReferenceView: NSView?
    private var heightConstraint: NSLayoutConstraint?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()

        guard window != nil else {
            uninstall()
            return
        }

        // WindowGroup 建立期间 contentView 的父视图可能还不是最终的 NSThemeFrame；
        // 下一轮主线程循环再安装，避免把滤镜留在 SwiftUI contentView 的错误层级。
        DispatchQueue.main.async { [weak self] in
            self?.installIfPossible()
        }
    }

    override func viewDidMoveToSuperview() {
        super.viewDidMoveToSuperview()
        installIfPossible()
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        // 代表视图只是生命周期锚点，不能截获窗口中的点击和滚动事件。
        nil
    }

    func installIfPossible() {
        guard let window, let contentView = window.contentView else { return }

        // fullSizeContentView 下，contentView 的父视图才同时包含标题栏 chrome 和正文；
        // 将 blurView 放在这个父视图上，才能实现“正文被模糊、toolbar 保持清晰”的层级。
        let overlayHost = contentView.superview ?? contentView

        if hostView === overlayHost,
           contentReferenceView === contentView,
           backdropView.superview === overlayHost {
            updateBackdropGeometry()
            return
        }

        uninstall()
        hostView = overlayHost
        contentReferenceView = contentView

        backdropView.translatesAutoresizingMaskIntoConstraints = false
        backdropView.titlebarHeight = titlebarHeight
        backdropView.fadeHeight = fadeHeight
        backdropView.maxBlurRadius = maxBlurRadius
        backdropView.isEnabled = isEnabled

        // 正常路径把滤镜插到 contentView 上方；只有 contentView 没有父视图时才使用
        // 自身作为 host，这个分支仅用于窗口初始化瞬间，后续会在下一轮重新挂载。
        overlayHost.addSubview(
            backdropView,
            positioned: .above,
            relativeTo: overlayHost === contentView ? nil : contentView
        )

        // 视觉规格要求顶栏与系统标题栏同一层厚度；fadeHeight 是这 52pt 内部的
        // 下缘过渡，不得再向正文方向额外叠一条可见高度。
        let constraint = backdropView.heightAnchor.constraint(equalToConstant: titlebarHeight)
        heightConstraint = constraint

        NSLayoutConstraint.activate([
            // contentView 已经通过 fullSizeContentView 延伸到标题栏下方；overlay 的顶部
            // 与它一致，固定高度只覆盖标题栏这一层，避免遮住导航栏和正文首行。
            backdropView.topAnchor.constraint(equalTo: contentView.topAnchor),
            backdropView.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            backdropView.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            constraint
        ])

        updateBackdropGeometry()
    }

    func uninstall() {
        heightConstraint?.isActive = false
        heightConstraint = nil
        backdropView.removeFromSuperview()
        hostView = nil
        contentReferenceView = nil
    }

    private func updateBackdropGeometry() {
        backdropView.titlebarHeight = titlebarHeight
        backdropView.fadeHeight = fadeHeight
        backdropView.maxBlurRadius = maxBlurRadius
        backdropView.isEnabled = isEnabled
    }
}

/// 位于窗口 frame 层级的透明滤镜视图。
///
/// 它不填充颜色、不使用 layer mask，只通过 NSView.backgroundFilters 过滤正后方的
/// SwiftUI 内容。CIMaskedVariableBlur 的灰度 mask 直接控制 blur radius，因此过渡区
/// 是“同一份内容逐渐减弱模糊”，不会出现模糊副本透明后露出清晰原图的白带/硬边。
private final class ProgressiveTitlebarBlurView: NSView {
    var titlebarHeight: CGFloat = 52 {
        didSet { updateFilterIfNeeded(force: true) }
    }

    var fadeHeight: CGFloat = 24 {
        didSet { updateFilterIfNeeded(force: true) }
    }

    var maxBlurRadius: CGFloat = 16 {
        didSet { updateFilterIfNeeded(force: true) }
    }

    var isEnabled: Bool = true {
        didSet { updateFilterIfNeeded(force: true) }
    }

    private var previousSize: CGSize = .zero

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
        // 先把 backgroundFilters 的合成边界限制在标题栏 view 的 bounds 内，作为验证
        // 滤镜是否越过目标区域的明确边界；主画布是否仍被影响要由窗口截图确认。
        layer?.masksToBounds = true
    }

    required init?(coder: NSCoder) {
        nil
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        // 滤镜层必须完全穿透鼠标和滚轮事件，让 toolbar 与正文照常接收输入。
        nil
    }

    override func layout() {
        super.layout()
        updateFilterIfNeeded()
    }

    private func updateFilterIfNeeded(force: Bool = false) {
        let size = bounds.size
        guard size.width > 0, size.height > 0 else { return }

        // 本轮先用滤镜层局部坐标构造 mask，验证此前窗口坐标与 background filter 的
        // extent 是否错位；这是一项待截图确认的假设，不把它当作已证实的系统行为。
        let maskRect = bounds
        guard force || size != previousSize else { return }
        previousSize = size

        guard isEnabled, maxBlurRadius > 0 else {
            backgroundFilters = []
            return
        }

        let transitionHeight = min(max(0, fadeHeight), size.height)
        let gradientStart = maskRect.minY + size.height - transitionHeight
        let gradientEnd = maskRect.maxY

        // Core Image 坐标的 Y 轴向上：标题栏底部 24pt 从黑到白，代表从无模糊
        // 快速过渡到最大模糊；其上方自然保持白色（最大 blur）。
        let gradient = CIFilter.smoothLinearGradient()
        gradient.color0 = CIColor.black
        gradient.color1 = CIColor.white
        gradient.point0 = CGPoint(x: 0, y: gradientStart)
        gradient.point1 = CGPoint(x: 0, y: gradientEnd)

        let blur = CIFilter.maskedVariableBlur()
        blur.radius = Float(maxBlurRadius)
        blur.mask = gradient.outputImage?.cropped(to: maskRect)

        // 按 AppKit 公开 API 设置 backgroundFilters，让系统自动提供正后方的 inputImage；
        // 不修改 layerUsesCoreImageFilters，也不复用已挂载的 CIFilter 实例。
        backgroundFilters = [blur]
    }
}
