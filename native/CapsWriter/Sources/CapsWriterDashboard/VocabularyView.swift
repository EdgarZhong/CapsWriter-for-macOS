import AppKit
import SwiftUI

// MARK: - 桥接协议模型
// 与 tools/dashboard_vocabulary.py 的 JSON 输出一一对应；
// 进程调用模式照抄 ResourceStore：development-paths.json 定位仓库与 Python，
// 合并 stdout/stderr 持续读取，取最后一行 JSON，主线程不阻塞。

/// 开发桥接路径配置（与 ResourceStore 中同名结构同构；该结构为 fileprivate，此处独立声明）。
private struct VocabularyPaths: Decodable {
    let root: String
    let python: String
}

/// 词库行：kind 为 entry 时按文件类型附带 term/aliases 或 pattern/replacement。
struct VocabularyEntry: Decodable {
    let file: String
    let index: Int
    let raw: String
    let kind: String
    let term: String?
    let aliases: [String]?
    let pattern: String?
    let replacement: String?
}

/// 单个词库文件的列举结果；exists=false 表示文件缺失（不算错误）。
struct VocabularyFileListing: Decodable {
    let exists: Bool
    let entries: [VocabularyEntry]
}

/// list / save 共用响应：save 成功时带 saved=true 与最新 files。
private struct VocabularyResponse: Decodable {
    let files: [String: VocabularyFileListing]?
    let saved: Bool?
    let error: String?
}

// MARK: - 编辑草稿

/// 界面编辑用的行草稿：注释与空行原样保留 raw 只读展示；
/// 条目行拆成字段编辑，保存时再拼装回原始行文本。
struct VocabularyDraftLine: Identifiable, Equatable {
    let id = UUID()
    /// "entry" / "comment" / "blank"，与桥接协议一致
    var kind: String
    /// 注释与空行的原始文本，保存时原样写回
    var raw: String = ""
    /// 热词目标词（hot / server）
    var term: String = ""
    /// 别名编辑文本：界面用顿号、逗号分隔输入，写回时统一转换为 " | " 分隔
    var aliasesText: String = ""
    /// 规则查找模式（rule）
    var pattern: String = ""
    /// 规则替换式（rule）
    var replacement: String = ""

    init(entry: VocabularyEntry) {
        kind = entry.kind
        raw = entry.raw
        term = entry.term ?? ""
        // 展示时别名统一用顿号连接，与输入提示一致
        aliasesText = (entry.aliases ?? []).joined(separator: "、")
        pattern = entry.pattern ?? ""
        replacement = entry.replacement ?? ""
    }

    /// 新增空白条目草稿
    static func newEntry() -> VocabularyDraftLine { VocabularyDraftLine(kind: "entry") }

    private init(kind: String) { self.kind = kind }

    /// 把别名编辑文本拆成别名数组：接受顿号、中英文逗号，去掉空白与空项
    var aliases: [String] {
        aliasesText
            .replacingOccurrences(of: "，", with: "、")
            .replacingOccurrences(of: ",", with: "、")
            .split(separator: "、")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    /// 按文件类型拼装回写盘用的原始行文本；
    /// 热词分隔符统一写为半角 " | "（与客户端运行时 hot_phoneme.py 的解析一致）。
    func rawLine(file: String) -> String {
        guard kind == "entry" else { return raw }
        if file == "rule" {
            return "\(pattern) = \(replacement)"
        }
        return ([term] + aliases).joined(separator: " | ")
    }
}

// MARK: - 状态仓库

/// 词库编辑状态：修改全部暂存内存，显式保存才经桥接写盘。
@MainActor
final class VocabularyStore: ObservableObject {
    /// 三个词库文件的草稿行（键为 hot / rule / server）；View 直接编辑，isDirty 对比快照
    @Published var drafts: [String: [VocabularyDraftLine]] = [:]
    /// 上次加载/保存成功时的快照，用于判断未保存修改
    private var savedSnapshot: [String: [VocabularyDraftLine]] = [:]
    /// 缺失的词库文件（exists=false），界面提示保存时会新建
    @Published private(set) var missing: Set<String> = []
    @Published var busy = false
    @Published var error: String?
    @Published var notice: String?

    /// 是否有未保存修改（任一文件）
    var hasUnsavedChanges: Bool { VocabularySection.allCases.contains { isDirty($0.id) } }
    func isDirty(_ file: String) -> Bool { drafts[file] != savedSnapshot[file] }

    func load() {
        perform(action: "list", file: nil, lines: nil) { [self] response in
            applyAll(response)
            notice = nil
        }
    }

    func save(_ file: String) {
        let lines = (drafts[file] ?? []).map { $0.rawLine(file: file) }
        perform(action: "save", file: file, lines: lines) { [self] response in
            applyAll(response)
            notice = "已保存，修改即刻写盘。"
        }
    }

    private func applyAll(_ response: VocabularyResponse) {
        guard let files = response.files else { return }
        for (key, listing) in files {
            drafts[key] = listing.entries.map(VocabularyDraftLine.init(entry:))
        }
        savedSnapshot = drafts
        missing = Set(files.filter { !$0.value.exists }.map(\.key))
    }

    /// 子进程在后台执行并持续读取合并输出，避免日志塞满管道阻塞桥接进程。
    private func perform(action: String, file: String?, lines: [String]?,
                         completion: @escaping (VocabularyResponse) -> Void) {
        guard !busy else { return }
        busy = true
        error = nil
        Task {
            do {
                let response = try await Task.detached(priority: .userInitiated) {
                    try Self.run(action: action, file: file, lines: lines)
                }.value
                completion(response)
            } catch {
                self.error = error.localizedDescription
            }
            busy = false
        }
    }

    /// 同步执行桥接：list 无输入；save 通过 stdin 传入 {"file","lines"} JSON。
    private nonisolated static func run(action: String, file: String?, lines: [String]?) throws -> VocabularyResponse {
        guard let configURL = Bundle.main.url(forResource: "development-paths", withExtension: "json") else {
            throw failure("请通过构建脚本生成开发 App 后运行。")
        }
        let paths = try JSONDecoder().decode(VocabularyPaths.self, from: Data(contentsOf: configURL))
        // 构建脚本当前只打包 dashboard_resources.py；词库桥接优先取打包资源，
        // 缺失时退回仓库 tools/ 目录，保证开发包在脚本更新前即可使用。
        let helper = Bundle.main.url(forResource: "dashboard_vocabulary", withExtension: "py")
            ?? URL(fileURLWithPath: paths.root).appendingPathComponent("tools/dashboard_vocabulary.py")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: paths.python)
        process.arguments = [helper.path, action, "--root", paths.root]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        if let file, let lines {
            let payload = try JSONSerialization.data(withJSONObject: ["file": file, "lines": lines])
            let input = Pipe()
            process.standardInput = input
            try process.run()
            input.fileHandleForWriting.write(payload)
            try input.fileHandleForWriting.close()
        } else {
            try process.run()
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard let last = String(decoding: data, as: UTF8.self).split(separator: "\n").last,
              let response = try? JSONDecoder().decode(VocabularyResponse.self, from: Data(last.utf8)) else {
            throw failure("词库桥接未返回有效结果，请检查开发环境。")
        }
        if let message = response.error { throw failure(message) }
        guard process.terminationStatus == 0 else { throw failure("词库操作未完成，请重试。") }
        return response
    }

    private nonisolated static func failure(_ message: String) -> NSError {
        NSError(domain: "Dashboard", code: 5, userInfo: [NSLocalizedDescriptionKey: message])
    }
}

// MARK: - 分段定义

/// 三个词库分段：标题、图标与生效时机说明。
/// 生效时机依据客户端/服务端真实行为：
/// - hot.txt 与 hot-rule.txt 由客户端 HotwordManager 的 watchdog 监听，
///   保存后约 3 秒防抖自动热重载（core/client/hotword/manager.py）。
/// - hot-server.txt 仅在服务端加载模型时读取一次
///   （core/server/worker/model_loader.py），需重启服务端后生效。
enum VocabularySection: String, CaseIterable, Identifiable {
    case hot, rule, server
    var id: String { rawValue }
    var title: String {
        switch self {
        case .hot: return "客户端热词"
        case .rule: return "自定义规则"
        case .server: return "服务端热词"
        }
    }
    var effect: String {
        switch self {
        case .hot, .rule:
            return "保存后由正在运行的客户端自动热重载，数秒内生效，无需重启。"
        case .server:
            return "服务端热词在模型加载时读取，保存后需重启服务端才会生效。"
        }
    }
    var hint: String {
        switch self {
        case .hot: return "每行一个热词；别名用顿号或逗号分隔，保存后识别到别名会纠正为目标词。"
        case .rule: return "每行一条「查找模式 = 替换式」，左侧为正则表达式；保存时会整批校验，非法规则将被拒绝。"
        case .server: return "每行一个热词，进入服务端识别上下文；过短的英文热词容易误换，请谨慎添加。"
        }
    }
}

// MARK: - 词库视图

/// 词库管理页：分段切换三个词库文件，条目逐行编辑，注释与空行只读保留，
/// 修改暂存内存，点击「保存」才经 Python 桥接整文件写盘（写盘前桥接侧自动备份到 .archive/）。
struct VocabularyView: View {
    @StateObject private var store = VocabularyStore()
    @State private var section: VocabularySection = .hot

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("管理热词、别名与替换规则，让识别结果更贴近你的表达。")
                .foregroundStyle(.secondary)

            // 分段切换只影响展示，各文件修改独立暂存，互不打断
            Picker("词库", selection: $section) {
                ForEach(VocabularySection.allCases) { item in
                    Text(item.title).tag(item)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            VStack(alignment: .leading, spacing: 0) {
                header
                Divider().padding(.horizontal, 20)
                rows
                Divider().padding(.horizontal, 20)
                footer
            }
            .modifier(ContentSurface())

            if store.hasUnsavedChanges {
                Label("有未保存修改；切换分段不会丢失，点击对应分段的「保存」后写盘。", systemImage: "exclamationmark.circle")
                    .font(.callout).foregroundStyle(.orange)
            }
            if let notice = store.notice {
                Label(notice, systemImage: "checkmark.circle")
                    .font(.callout).foregroundStyle(.green)
            }
            if let error = store.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.orange).textSelection(.enabled)
            }
            Label(section.effect, systemImage: "info.circle")
                .font(.callout).foregroundStyle(.secondary)
        }
        .task { store.load() }
    }

    // 分段卡片头部：说明文案 + 缺失提示 + 操作按钮
    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text(section.title).font(.headline)
                Text(section.hint).font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if store.missing.contains(section.id) {
                    Text("词库文件尚不存在，保存后将自动创建。")
                        .font(.caption).foregroundStyle(.orange)
                }
            }
            Spacer()
            if store.busy { ProgressView().controlSize(.small) }
            Button {
                store.drafts[section.id, default: []].append(.newEntry())
            } label: {
                Label("添加", systemImage: "plus")
            }
            .disabled(store.busy)
            Button("保存") { store.save(section.id) }
                .buttonStyle(.borderedProminent)
                .disabled(store.busy || !store.isDirty(section.id))
                .help(store.isDirty(section.id) ? "把当前分段的修改写盘" : "当前分段没有修改")
        }
        .padding(20)
    }

    // 条目逐行编辑；注释与空行只读展示，保证写回后原注释结构不丢失
    @ViewBuilder private var rows: some View {
        let lines = store.drafts[section.id] ?? []
        if lines.isEmpty && !store.busy {
            Text(store.error == nil ? "词库文件为空或尚未加载。" : "")
                .font(.callout).foregroundStyle(.secondary).padding(20)
        }
        ForEach(Array(lines.enumerated()), id: \.element.id) { index, line in
            if index > 0 { Divider().padding(.horizontal, 20) }
            row(at: index, line: line)
        }
    }

    @ViewBuilder
    private func row(at index: Int, line: VocabularyDraftLine) -> some View {
        let binding = lineBinding(at: index)
        HStack(spacing: 12) {
            switch line.kind {
            case "comment":
                // 注释行只读展示，保持原样写回
                Text(line.raw).font(.callout).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.tail)
            case "blank":
                Text("（空行）").font(.callout).foregroundStyle(.tertiary)
            default:
                if section == .rule {
                    TextField("查找模式（正则）", text: binding.pattern)
                    Image(systemName: "arrow.right").foregroundStyle(.tertiary)
                    TextField("替换为", text: binding.replacement)
                } else {
                    TextField("热词", text: binding.term)
                        .frame(minWidth: 140)
                    TextField("别名（顿号或逗号分隔，可留空）", text: binding.aliasesText)
                }
                Button(role: .destructive) {
                    // 删除只移除待保存草稿，点击「保存」后才会写盘
                    store.drafts[section.id]?.removeAll { $0.id == line.id }
                } label: {
                    Image(systemName: "minus.circle")
                }
                .buttonStyle(.borderless)
                .disabled(store.busy)
                .help("从待保存列表移除")
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 10)
    }

    /// 安全生成行字段绑定：数组可能因并发刷新缩短，越界时给只读常量
    private func lineBinding(at index: Int) -> (pattern: Binding<String>, replacement: Binding<String>,
                                                term: Binding<String>, aliasesText: Binding<String>) {
        func field(_ keyPath: WritableKeyPath<VocabularyDraftLine, String>) -> Binding<String> {
            Binding(
                get: { store.drafts[section.id]?[safe: index]?[keyPath: keyPath] ?? "" },
                set: { value in
                    guard var lines = store.drafts[section.id], lines.indices.contains(index) else { return }
                    lines[index][keyPath: keyPath] = value
                    store.drafts[section.id] = lines
                }
            )
        }
        return (field(\.pattern), field(\.replacement), field(\.term), field(\.aliasesText))
    }

    // 卡片底部：条目统计
    private var footer: some View {
        let lines = store.drafts[section.id] ?? []
        let entries = lines.filter { $0.kind == "entry" }.count
        return Text("共 \(entries) 条\(section == .rule ? "规则" : "热词")，注释与空行保存时原样保留。")
            .font(.caption).foregroundStyle(.secondary)
            .padding(.horizontal, 20).padding(.vertical, 12)
    }
}

/// 数组安全下标：并发刷新时避免越界崩溃
private extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
