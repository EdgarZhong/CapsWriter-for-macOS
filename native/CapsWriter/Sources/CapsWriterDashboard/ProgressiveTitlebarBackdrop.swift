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

        // fullSizeContentView 下，contentView 的父视图同时包含标题栏 chrome 和正文。
        // 找到 NavigationSplitView 后，把滤镜放进分栏本身，位于侧边栏视图之后、
        // detail 视图之前；侧边栏由自己的材质和 z-order 保持清晰，滤镜仍能处理主画布。
        let rootHost = contentView.superview ?? contentView
        let splitView = findVerticalSplitView(in: rootHost)
        let overlayHost: NSView = splitView ?? rootHost

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

        if let splitView, let detailView = splitView.subviews.dropFirst().first {
            // 将滤镜插在 detail 视图上方；在 macOS 当前的 NSSplitView 绘制顺序中，
            // 这会落在侧栏与 detail 之间，侧栏保持清晰而主画布仍进入滤镜输入。
            splitView.addSubview(backdropView, positioned: .above, relativeTo: detailView)
        } else {
            // SwiftUI 层级尚未完成时退回根 host；下一次 installIfPossible 会重新挂载。
            rootHost.addSubview(backdropView, positioned: .above, relativeTo: contentView)
        }

        // 视觉规格要求顶栏与系统标题栏同一层厚度；fadeHeight 是这 52pt 内部的
        // 下缘过渡，不得再向正文方向额外叠一条可见高度。
        let constraint = backdropView.heightAnchor.constraint(equalToConstant: titlebarHeight)
        heightConstraint = constraint

        NSLayoutConstraint.activate([
            // 分栏或根 host 都已延伸到标题栏下方，固定高度只覆盖标题栏这一层。
            backdropView.topAnchor.constraint(equalTo: overlayHost.topAnchor),
            backdropView.leadingAnchor.constraint(equalTo: overlayHost.leadingAnchor),
            backdropView.trailingAnchor.constraint(equalTo: overlayHost.trailingAnchor),
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

    private func findVerticalSplitView(in view: NSView) -> NSSplitView? {
        if let splitView = view as? NSSplitView, splitView.isVertical {
            return splitView
        }
        for subview in view.subviews {
            if let splitView = findVerticalSplitView(in: subview) {
                return splitView
            }
        }
        return nil
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

        // backgroundFilters 工作在窗口合成上下文中，mask 必须使用窗口坐标；否则
        // 内容滚入标题栏时，局部 bounds 与 backdrop 输入图像会发生坐标错位。
        let maskRect = convert(bounds, to: nil)
        guard force || size != previousSize else { return }
        previousSize = size

        guard isEnabled, maxBlurRadius > 0 else {
            backgroundFilters = []
            return
        }

        let transitionHeight = min(max(0, fadeHeight), size.height)
        // mask 黑色代表 0 blur，白色代表 blur.radius 指定的最大模糊。
        // 标题栏最下沿从黑色开始，向上经过 24pt 渐变到白色；上方剩余区域
        // 保持白色平台，因此形成“底部渐进、上方持续最大模糊”的 52pt 效果区。
        let gradient = CIFilter.smoothLinearGradient()
        gradient.color0 = CIColor.black
        gradient.color1 = CIColor.white
        gradient.point0 = CGPoint(x: maskRect.midX, y: maskRect.minY)
        gradient.point1 = CGPoint(x: maskRect.midX, y: maskRect.minY + transitionHeight)

        let blur = CIFilter.maskedVariableBlur()
        blur.radius = Float(maxBlurRadius)
        blur.mask = gradient.outputImage?.cropped(to: maskRect)

        // 按 AppKit 公开 API 设置 backgroundFilters，让系统自动提供正后方的 inputImage；
        // 不修改 layerUsesCoreImageFilters，也不复用已挂载的 CIFilter 实例。
        backgroundFilters = [blur]
    }
}
