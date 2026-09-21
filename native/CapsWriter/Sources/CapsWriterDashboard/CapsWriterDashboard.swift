import AppKit
import SwiftUI
import DashboardCore

/// 独立开发窗口消费现有快照，不接管录音进程与系统快捷键。
@main
struct CapsWriterDashboard: App {
    var body: some Scene {
        WindowGroup("CapsWriter") {
            DashboardView().frame(minWidth: 860, minHeight: 640)
        // 参考图按 Retina 2x 测量约为 998×702pt；用整百数作为默认值，
        // 让新窗口落在同一尺寸档位，同时保留内容的最小可用尺寸。
        }.defaultSize(width: 1000, height: 700)
        .commands { SidebarCommands() }
        Window("CapsWriter 权限", id: "permissions") {
            PermissionsView()
        }.defaultSize(width: 540, height: 420)
            .windowResizability(.contentSize)
    }
}

private enum Page: String, CaseIterable, Identifiable {
    case overview = "概览", settings = "设置", vocabulary = "词库", inference = "推理方案", history = "转录历史"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .overview: return "waveform"
        case .settings: return "slider.horizontal.3"
        case .vocabulary: return "text.book.closed"
        case .inference: return "cpu"
        case .history: return "clock.arrow.circlepath"
        }
    }
    var subtitle: String {
        switch self {
        case .overview: return "让表达，自然发生。"
        case .settings: return "让 CapsWriter 更适合你的习惯。"
        case .vocabulary: return "熟悉你的用词，保留你的表达。"
        case .inference: return "在本机完成识别，让语音留在本机。"
        case .history: return "找回、复制并修订每一次转录。"
        }
    }
}

/// 生命周期由窗口任务管理，关闭窗口后取消轮询。
@MainActor
private final class DashboardState: ObservableObject {
    @Published var result = SnapshotReader().decode(Data(), now: Date())
    func refresh() { result = SnapshotReader().read() }
}

private struct DashboardView: View {
    @Environment(\.openWindow) private var openWindow
    @State private var selection: Page? = .overview
    @State private var sidebarVisibility: NavigationSplitViewVisibility = .all
    @StateObject private var model = DashboardState()
    @StateObject private var resources = ResourceStore()
    @AppStorage("dashboard.appearance") private var appearance = DashboardAppearance.system
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    private var page: Page { selection ?? .overview }
    private var readiness: Readiness { Readiness(model.result) }
    private var isSidebarCollapsed: Bool { sidebarVisibility == .detailOnly }

    var body: some View {
        // 透明 NSWindow 只做合成容器；磨砂背板是 SwiftUI 根 ZStack 的最底层。
        // window 容器背景清空 + 透明窗口 + 根层材质三者缺一，就会出现旧实现的
        // 「全不透明」或「标题栏白色残块」。macOS 无 navigation 系 containerBackground
        // placement，分列背景由窗口透明与各列自身背景设置承担。
        ZStack {
            // 辅助功能回退用整窗实色；常规外观共享同一块磨砂背板。
            if reduceTransparency {
                Color(nsColor: .windowBackgroundColor).ignoresSafeArea()
            } else {
                DashboardWindowBackdrop().ignoresSafeArea()
            }
            NavigationSplitView(columnVisibility: $sidebarVisibility) {
                sidebar.modifier(DashboardSidebarToggleRemoval())
            } detail: {
                // 干净的单层滚动区：窗口已开 fullSizeContentView + 透明标题栏（见
                // WindowConfigurator），ScrollView 内容自动延伸到标题栏下方，
                // 安全区内边距保证初始内容不被遮挡；窗口级 ProgressiveTitlebarBackdrop
                // 会在内容滚入标题栏时对其施加可变半径模糊，不在 ScrollView 内占布局高度。
                ScrollView {
                    VStack(alignment: .leading, spacing: 28) {
                        HStack {
                            Text(page.rawValue).font(.title.weight(.semibold))
                            Spacer()
                            if page == .inference {
                                Button { resources.refresh() } label: {
                                    Label("刷新", systemImage: "arrow.clockwise")
                                }.disabled(resources.busy).keyboardShortcut("r", modifiers: .command)
                            }
                        }
                        switch page {
                        case .overview: overview
                        case .settings: SettingsView()
                        case .inference: inference
                        case .vocabulary: VocabularyView()
                        case .history: HistoryView()
                        }
                    }
                    .padding(32)
                    .frame(maxWidth: 940, alignment: .leading).frame(maxWidth: .infinity)
                }
            }
            .toolbar {
                ToolbarItem(placement: .navigation) {
                    Button {
                        withAnimation {
                            sidebarVisibility = isSidebarCollapsed ? .all : .detailOnly
                        }
                    } label: {
                        Label(
                            isSidebarCollapsed ? "显示侧栏" : "隐藏侧栏",
                            systemImage: "sidebar.leading"
                        )
                    }
                    .help(isSidebarCollapsed ? "显示侧栏" : "隐藏侧栏")
                    .accessibilityLabel(isSidebarCollapsed ? "显示侧栏" : "隐藏侧栏")
                }
                if #available(macOS 26.0, *) {
                    // macOS 26 的系统 flexible spacer 只负责分隔 toolbar 两端，
                    // 不改变外观 Picker 的 SwiftUI 原生渲染环境。
                    ToolbarSpacer(.flexible)
                }
                ToolbarItem {
                    // 唯一外观入口：原生三段切换，不在侧栏或设置页重复。
                    Picker("外观", selection: $appearance) {
                        ForEach(DashboardAppearance.allCases) { mode in
                            Image(systemName: mode.symbol).tag(mode)
                                .help(mode.title).accessibilityLabel(mode.title)
                        }
                    }.pickerStyle(.segmented).labelsHidden().frame(width: 120)
                        .accessibilityLabel("窗口外观").help("跟随系统、浅色或深色")
                }
            }
            .modifier(DashboardWindowTitle(showTitle: isSidebarCollapsed))
        }
        .clearWindowContainerBackground()
        .hiddenWindowToolbarBackground()
        .background(WindowConfigurator())
        .background(
            ProgressiveTitlebarBackdrop(
                titlebarHeight: DashboardSurfaceStyle.titlebarHeight,
                fadeHeight: DashboardSurfaceStyle.titlebarFadeHeight,
                maxBlurRadius: DashboardSurfaceStyle.titlebarMaxBlurRadius,
                isEnabled: !reduceTransparency
            )
        )
        // 外观职责只在右上角 Picker；task(id:) 让每次选择变化都应用到当前 App。
        // system 分支写入 nil，交还给 macOS 自动切换亮/暗模式。
        .task(id: appearance) {
            appearance.applyToApplication()
        }
        .task {
            resources.refresh()
            while !Task.isCancelled {
                model.refresh()
                do { try await Task.sleep(nanoseconds: 2_000_000_000) }
                catch { break }
            }
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 20) {
            // 标题保持单行，避免窗口记忆了窄列宽时品牌名被拆成两行。
            VStack(alignment: .leading, spacing: 2) {
                Text("CapsWriter")
                    .font(.title.weight(.bold))
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                Text("for macOS")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)
            List(selection: $selection) {
                ForEach(Page.allCases) { item in
                    Label(item.rawValue, systemImage: item.symbol)
                        .padding(.vertical, 8).tag(item)
                }
            }.listStyle(.sidebar).scrollContentBackground(.hidden)
            Label("本地语音输入", systemImage: "lock.shield")
                .font(.caption).foregroundStyle(.secondary).padding(20)
        }
        // 让原生侧栏使用完整背板，不再覆盖第二份 behindWindow 材质。
        .background(Color.clear)
        // 内容声明最小宽度，避免 macOS 记住旧的窄分栏值后再次压缩品牌标题。
        .frame(minWidth: 190, alignment: .leading)
        // 分栏分别参与窗口工具栏配置，统一隐藏染色背景，避免侧栏保留独立亮带。
        .hiddenWindowToolbarBackground()
        // 参考图按 Retina 2x 折算侧栏约 212pt；ideal 明确默认宽度，仍保留拖动扩展空间。
        .navigationSplitViewColumnWidth(min: 190, ideal: 212, max: 280)
    }

    private var overview: some View {
        VStack(alignment: .leading, spacing: 28) {
            HStack(alignment: .center, spacing: 24) {
                // 正常状态仅使用大号原生 Caps Lock 图标，不模拟键帽。
                Image(systemName: readiness.ready ? "capslock" : readiness.symbol)
                    .font(.system(size: 48, weight: .light))
                    .foregroundStyle(readiness.issues.isEmpty ? Color.primary : Color.orange)
                    .frame(width: 64).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 8) {
                    Text(readiness.title).font(.title2.weight(.semibold))
                    Text(readiness.detail).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }.padding(.vertical, 16)

            if !readiness.issues.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(readiness.issues.enumerated()), id: \.element.id) { index, issue in
                        if index > 0 { Divider().padding(.leading, 48) }
                        HStack(spacing: 14) {
                            Image(systemName: issue.symbol).frame(width: 24).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(issue.title).font(.body.weight(.medium))
                                Text(issue.detail).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            }
                            Spacer()
                            Button(actionLabel(issue.action)) { handle(issue.action) }
                        }.padding(20)
                    }
                }.modifier(ContentSurface())
            }

            VStack(alignment: .leading, spacing: 0) {
                sectionHeading("运行状态")
                summaryRow("识别服务", symbol: "cpu", value: freshValue(model.result.snapshot?.serverConnected, success: "已连接")) {
                    selection = .inference
                }
                Divider().padding(.leading, 52)
                summaryRow("输入权限", symbol: "hand.point.up.left", value: permissionSummary) {
                    openWindow(id: "permissions")
                }
                Divider().padding(.leading, 52)
                summaryRow("推理资源", symbol: "externaldrive", value: resourceSummary) {
                    selection = .inference
                }
            }.modifier(ContentSurface())
            Text("按住 Caps Lock 说话，松开完成识别。轻按仍可切换大小写。")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    private var permissionSummary: String {
        guard let snapshot = model.result.snapshot, model.result.availability != .stale else { return "待确认" }
        return snapshot.accessibilityOK && snapshot.microphoneOK && (snapshot.permissionPhase == "ready" || snapshot.permissionPhase == nil)
            ? "已就绪" : "需要检查"
    }
    private var resourceSummary: String {
        if resources.busy { return "正在检测" }
        if resources.error != nil { return "检测失败" }
        let count = resources.plans.filter(\.ready).count
        return "\(count) 个方案资源齐备"
    }
    private func freshValue(_ value: Bool?, success: String) -> String {
        if model.result.availability == .stale { return "状态已过期" }
        return value == true ? success : "未连接"
    }
    private func sectionHeading(_ text: String) -> some View {
        Text(text).font(.headline).padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 8)
    }
    private func summaryRow(_ title: String, symbol: String, value: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: symbol).frame(width: 20).foregroundStyle(.secondary)
                Text(title)
                Spacer()
                Text(value).foregroundStyle(.secondary)
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
            }.padding(20).contentShape(Rectangle())
        }.buttonStyle(.plain)
    }

    private var inference: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("检测本地模型与运行时，按需下载缺失资源。")
                .foregroundStyle(.secondary)
            if resources.plans.isEmpty && resources.busy {
                ProgressView("正在检测资源…").padding(.vertical, 24)
            }
            VStack(spacing: 0) {
                ForEach(Array(resources.plans.enumerated()), id: \.element.id) { index, plan in
                    if index > 0 { Divider().padding(.horizontal, 20) }
                    VStack(alignment: .leading, spacing: 16) {
                        HStack(alignment: .top, spacing: 14) {
                            Image(systemName: "cpu").font(.title2).foregroundStyle(.secondary).frame(width: 32)
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text(plan.title).font(.headline)
                                    Text(plan.precision).font(.caption.monospaced()).foregroundStyle(.secondary)
                                }
                                Label(plan.status, systemImage: plan.ready ? "checkmark.circle" : "arrow.down.circle")
                                    .font(.callout).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if !plan.ready {
                                Button(resources.downloading == plan.id ? "下载中…" : "下载") { resources.download(plan) }
                                    .disabled(resources.busy).buttonStyle(.borderedProminent)
                            }
                        }
                        if let warning = plan.warning {
                            Label(warning, systemImage: "exclamationmark.triangle")
                                .font(.caption).foregroundStyle(.orange)
                        }
                        HStack {
                            Text("模型 \(plan.modelReady ? "齐备" : "缺失或不完整") · 运行时 \(plan.runtimeReady ? "可用" : "缺失")")
                                .font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Button { resources.openDirectory(plan) } label: {
                                Label("打开资源目录", systemImage: "folder")
                            }.buttonStyle(.borderless)
                        }
                    }.padding(24)
                }
            }.modifier(ContentSurface())
            if resources.downloading != nil {
                ProgressView("正在准备运行时与模型，完成后自动检查…").font(.callout)
            }
            resourceError
            if let date = resources.lastChecked {
                Text("上次检测：\(date.formatted(date: .omitted, time: .standard))")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("资源齐备表示本地文件与运行时检查通过，不代表该方案已加载。当前听写仍沿用现有客户端配置。")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var resourceError: some View {
        if let error = resources.error {
            Label(error, systemImage: "exclamationmark.triangle")
                .font(.callout).foregroundStyle(.orange).textSelection(.enabled)
        }
    }
    private func actionLabel(_ action: ReadinessAction) -> String {
        switch action {
        case .accessibility, .inputMonitoring, .microphone: return "前往授权"
        case .inference: return "检查推理方案"
        case .settings: return "检查客户端"
        case .refresh: return "重新检测"
        }
    }
    private func handle(_ action: ReadinessAction) {
        switch action {
        case .inference: selection = .inference; resources.refresh()
        case .settings: openWindow(id: "permissions")
        case .refresh: model.refresh(); resources.refresh()
        case .accessibility, .inputMonitoring, .microphone: openWindow(id: "permissions")
        }
    }
}
