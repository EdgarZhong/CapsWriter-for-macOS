import AppKit
import SwiftUI

/// 设置页字段元数据完全来自 Python 桥接（tools/dashboard_settings.py），
/// Swift 侧不维护字段清单，避免两处口径漂移。
struct SettingsField: Decodable, Identifiable {
    let key: String
    let group: String
    let title: String
    let kind: String
    let choices: [String]?
    let value: SettingValue
    let defaultValue: SettingValue
    let needsRestart: Bool
    let help: String
    let defaultOnly: Bool?
    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, group, title, kind, choices, value, needsRestart, help, defaultOnly
        case defaultValue = "default"
    }
}

/// 配置值在 JSON 中是异构标量；解码按 bool→int→double→string 顺序尝试，
/// 与 Python 端 bool 是 int 子类的顺序同理，避免 true 被吞成 1。
enum SettingValue: Decodable, Equatable {
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Bool.self) { self = .bool(value); return }
        if let value = try? container.decode(Int.self) { self = .int(value); return }
        if let value = try? container.decode(Double.self) { self = .double(value); return }
        self = .string(try container.decode(String.self))
    }

    /// 文本类字段（int/float/string）的输入框展示形态。
    var text: String {
        switch self {
        case .bool(let value): return value ? "true" : "false"
        case .int(let value): return String(value)
        case .double(let value): return String(value)
        case .string(let value): return value
        }
    }

    /// 保存时还原成 JSON 原生标量，保证 Python 端类型校验通过。
    var jsonObject: Any {
        switch self {
        case .bool(let value): return value
        case .int(let value): return value
        case .double(let value): return value
        case .string(let value): return value
        }
    }

    /// 数值字段的脏检测需要跨 int/double 比较（JSON 可能把 2.0 收成 int）。
    var number: Double? {
        switch self {
        case .int(let value): return Double(value)
        case .double(let value): return value
        default: return nil
        }
    }
}

private struct SettingsListResponse: Decodable {
    let fields: [SettingsField]?
    let saved: Bool?
    let message: String?
    let error: String?
}

/// 与 ResourceStore 同一套进程调用模式：子进程后台执行、合并输出取最后一行 JSON。
/// 修改先暂存内存，显式保存才落盘，避免每次击键都改写用户配置文件。
@MainActor
final class SettingsStore: ObservableObject {
    @Published var fields: [SettingsField] = []
    /// bool/choice 类字段的暂存修改；文本类字段统一走 drafts 以保留用户原始输入。
    @Published var edits: [String: SettingValue] = [:]
    @Published var drafts: [String: String] = [:]
    @Published var busy = false
    @Published var error: String?
    @Published var message: String?
    /// 桥接只返回默认值时的统一提示，让用户知道界面展示的并非本机生效配置。
    @Published var defaultOnly = false

    func load() {
        guard !busy, fields.isEmpty else { return }
        busy = true; error = nil
        perform(action: "list", payload: nil) { [weak self] response in
            guard let self else { return }
            self.fields = response.fields ?? []
            self.defaultOnly = self.fields.contains { $0.defaultOnly == true }
            self.edits.removeAll(); self.drafts.removeAll()
            self.busy = false
        }
    }

    func save() {
        guard !busy else { return }
        let payload = pendingPayload()
        guard !payload.isEmpty else { return }
        busy = true; error = nil; message = nil
        perform(action: "save", payload: payload) { [weak self] response in
            guard let self else { return }
            if let fields = response.fields {
                self.fields = fields
                self.defaultOnly = fields.contains { $0.defaultOnly == true }
                self.edits.removeAll(); self.drafts.removeAll()
            }
            self.message = response.message
            self.busy = false
        }
    }

    // MARK: - 绑定辅助

    func boolBinding(_ field: SettingsField) -> Binding<Bool> {
        Binding(
            get: {
                if case .bool(let value) = self.edits[field.key] { return value }
                if case .bool(let value) = field.value { return value }
                return false
            },
            set: { self.edits[field.key] = .bool($0) }
        )
    }

    func choiceBinding(_ field: SettingsField) -> Binding<String> {
        Binding(
            get: {
                if case .string(let value) = self.edits[field.key] { return value }
                if case .string(let value) = field.value { return value }
                return field.choices?.first ?? ""
            },
            set: { self.edits[field.key] = .string($0) }
        )
    }

    func textBinding(_ field: SettingsField) -> Binding<String> {
        Binding(
            get: { self.drafts[field.key] ?? field.value.text },
            set: { self.drafts[field.key] = $0 }
        )
    }

    /// 文本输入的合法性按字段类型校验；非法输入保留在界面上但阻止保存。
    func textValid(_ field: SettingsField) -> Bool {
        guard let draft = drafts[field.key] else { return true }
        switch field.kind {
        case "int": return Int(draft.trimmingCharacters(in: .whitespaces)) != nil
        case "float": return Double(draft.trimmingCharacters(in: .whitespaces)) != nil
        default: return true
        }
    }

    /// 当前生效的暂存值（未修改时取桥接返回值），用于脏检测；非法文本返回 nil。
    func currentValue(_ field: SettingsField) -> SettingValue? {
        if let edit = edits[field.key] { return edit }
        guard let draft = drafts[field.key], draft != field.value.text else { return field.value }
        switch field.kind {
        case "int":
            guard let value = Int(draft.trimmingCharacters(in: .whitespaces)) else { return nil }
            return .int(value)
        case "float":
            guard let value = Double(draft.trimmingCharacters(in: .whitespaces)) else { return nil }
            return .double(value)
        default:
            return .string(draft)
        }
    }

    func isDirty(_ field: SettingsField) -> Bool {
        guard let current = currentValue(field) else { return true } // 非法输入也算未保存
        // 数值字段按数值比较：JSON 解码可能把 2.0 收成 int，直接比枚举会产生误报。
        if field.kind == "int" || field.kind == "float" {
            return current.number != field.value.number
        }
        return current != field.value
    }

    /// 未保存修改数量；非法输入计入，提示用户还有改动未落地。
    var dirtyCount: Int { fields.filter { isDirty($0) }.count }
    var hasInvalidDraft: Bool { fields.contains { drafts[$0.key] != nil && !textValid($0) } }

    private func pendingPayload() -> [String: Any] {
        var payload: [String: Any] = [:]
        for field in fields where isDirty(field) && textValid(field) {
            if let current = currentValue(field) { payload[field.key] = current.jsonObject }
        }
        return payload
    }

    // MARK: - 桥接进程

    private func perform(action: String, payload: [String: Any]?,
                         completion: @escaping @MainActor (SettingsListResponse) -> Void) {
        Task {
            do {
                // Swift 6 的并发检查禁止把含有 Any 的主 actor 字典直接送入
                // detached task；先在主 actor 上编码成不可变 Data，保持桥接 JSON
                // 结构不变，同时让后台进程调用满足 Sendable 边界。
                let payloadData = try payload.map { try JSONSerialization.data(withJSONObject: $0) }
                let response = try await Task.detached(priority: .userInitiated) {
                    try Self.runBridge(action: action, payloadData: payloadData)
                }.value
                completion(response)
            } catch {
                self.error = error.localizedDescription
                self.busy = false
            }
        }
    }

    // 进程执行在后台线程完成，必须脱离 MainActor 隔离，否则阻塞主线程。
    nonisolated private static func runBridge(action: String, payloadData: Data?) throws -> SettingsListResponse {
        guard let configURL = Bundle.main.url(forResource: "development-paths", withExtension: "json") else {
            throw NSError(domain: "Dashboard", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "请通过构建脚本生成开发 App 后运行。"])
        }
        struct Paths: Decodable { let root: String; let python: String }
        let paths = try JSONDecoder().decode(Paths.self, from: Data(contentsOf: configURL))
        // 构建脚本目前只打包 dashboard_resources.py；设置脚本先找 Bundle，
        // 找不到时回退到仓库 tools/ 目录，保证开发包在脚本更新前即可用。
        let helper = Bundle.main.url(forResource: "dashboard_settings", withExtension: "py")?.path
            ?? (paths.root as NSString).appendingPathComponent("tools/dashboard_settings.py")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: paths.python)
        process.arguments = [helper, action, "--root", paths.root]
        let pipe = Pipe()
        // 合并输出后持续读取，防止日志塞满管道；结果取最后一行 JSON。
        process.standardOutput = pipe; process.standardError = pipe
        var stdin: Pipe?
        if payloadData != nil {
            let input = Pipe()
            process.standardInput = input
            stdin = input
        }
        try process.run()
        if let stdin, let payloadData {
            stdin.fileHandleForWriting.write(payloadData)
            try? stdin.fileHandleForWriting.close()
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let lines = String(decoding: data, as: UTF8.self).split(separator: "\n")
        guard let last = lines.last,
              let response = try? JSONDecoder().decode(SettingsListResponse.self, from: Data(last.utf8)) else {
            throw NSError(domain: "Dashboard", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "设置桥接未返回有效结果。请检查开发环境。"])
        }
        if let message = response.error {
            throw NSError(domain: "Dashboard", code: 3, userInfo: [NSLocalizedDescriptionKey: message])
        }
        return response
    }
}

/// 选项值的中文展示仅用于界面；字段清单与取值仍全部来自桥接。
private func choiceLabel(_ value: String) -> String {
    switch value {
    case "zh-hant": return "标准繁体"
    case "zh-tw": return "台湾繁体"
    case "zh-hk": return "香港繁体"
    case "default": return "跟随系统默认"
    case "builtin": return "Mac 内建麦克风"
    case "auto": return "自动判断"
    case "chinese": return "中文"
    case "english": return "英文"
    case "japanese": return "日文"
    default: return value
    }
}

/// 设置页：数据驱动渲染，分「客户端」「识别服务」两组卡片，
/// 行间节奏沿用概览页 summaryRow 的 20pt 内边距与分隔线样式。
struct SettingsView: View {
    @StateObject private var store = SettingsStore()

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            if store.fields.isEmpty && store.busy {
                ProgressView("正在读取设置…").padding(.vertical, 24)
            }
            if store.defaultOnly {
                // 桥接导入配置失败时只拿到发布默认值，必须明示，避免误导用户。
                Label("未能读取本机配置，以下展示发布默认值，保存前请确认。",
                      systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.orange)
            }
            groupCard(group: "client", title: "客户端",
                      subtitle: "快捷键、上屏方式与热词等本机行为。")
            groupCard(group: "server", title: "识别服务",
                      subtitle: "识别结果排版与 MLX 推理驻留策略。")
            footer
        }
        .task { store.load() }
    }

    private func groupCard(group: String, title: String, subtitle: String) -> some View {
        let rows = store.fields.filter { $0.group == group }
        return VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 8)
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, field in
                Divider().padding(.horizontal, 20)
                settingRow(field)
            }
            if rows.isEmpty && !store.busy {
                Text("暂无可配置项").font(.callout).foregroundStyle(.secondary).padding(20)
            }
        }.modifier(ContentSurface())
    }

    private func settingRow(_ field: SettingsField) -> some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    Text(field.title).font(.body.weight(.medium))
                    if store.isDirty(field) {
                        // 未保存标记：让用户清楚哪些改动还没落盘。
                        Text("未保存").font(.caption2).foregroundStyle(.orange)
                    }
                }
                Text(field.help).font(.caption).foregroundStyle(.secondary)
                if field.needsRestart {
                    Text("重启后生效").font(.caption2).foregroundStyle(.tertiary)
                }
                if !store.textValid(field) {
                    Text(field.kind == "int" ? "请输入整数" : "请输入数值")
                        .font(.caption).foregroundStyle(.red)
                }
            }
            Spacer(minLength: 12)
            control(for: field)
        }.padding(20)
    }

    @ViewBuilder
    private func control(for field: SettingsField) -> some View {
        switch field.kind {
        case "bool":
            Toggle("", isOn: store.boolBinding(field))
                .labelsHidden().toggleStyle(.switch).disabled(store.busy)
        case "choice":
            Picker("", selection: store.choiceBinding(field)) {
                ForEach(field.choices ?? [], id: \.self) { choice in
                    Text(choiceLabel(choice)).tag(choice)
                }
            }.labelsHidden().frame(minWidth: 160).disabled(store.busy)
        default:
            TextField("", text: store.textBinding(field))
                .textFieldStyle(.roundedBorder)
                .frame(width: field.kind == "string" ? 220 : 120)
                .multilineTextAlignment(.trailing)
                .disabled(store.busy)
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 16) {
                if store.dirtyCount > 0 {
                    Text("有 \(store.dirtyCount) 项未保存的修改")
                        .font(.callout).foregroundStyle(.orange)
                }
                Spacer()
                if store.busy { ProgressView().controlSize(.small) }
                Button("复原") {
                    // 放弃暂存修改，回到桥接返回的本机生效值。
                    store.edits.removeAll(); store.drafts.removeAll(); store.message = nil
                }.disabled(store.busy || store.dirtyCount == 0)
                Button("保存") { store.save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(store.busy || store.dirtyCount == 0 || store.hasInvalidDraft)
                    .keyboardShortcut("s", modifiers: .command)
            }
            if let message = store.message {
                Label(message, systemImage: "checkmark.circle")
                    .font(.callout).foregroundStyle(.green)
            }
            if let error = store.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.orange).textSelection(.enabled)
            }
        }
    }
}
