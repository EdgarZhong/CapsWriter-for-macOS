import AppKit
import SwiftUI

/// 开发桥接的路径由构建脚本提供；与 ResourceStore 同一约定，不从窗口猜测仓库位置。
/// ResourceStore 中的同名结构是其文件私有类型，这里为本文件单独声明。
private struct DevelopmentPaths: Decodable {
    let root: String
    let python: String
}

/// 桥接只输出显示字段；revision 是保存时的冲突校验凭据，界面只透传、不自行计算。
struct HistoryEntry: Decodable, Identifiable {
    let id: String
    let date: String
    let text: String
    let source: String
    let corrected: Bool
    let revision: String
    let hasAudio: Bool
}

/// list 返回 entries；save 额外返回 saved/message；失败时只有 error，三种形态共用一个解码模型。
struct HistoryResponse: Decodable {
    let entries: [HistoryEntry]?
    let saved: Bool?
    let message: String?
    let error: String?
}

/// 历史读写全部经过 dashboard_history.py 桥接，界面不直接触碰日记与标注文件。
/// 刷新与保存共用同一条子进程通道，沿用 ResourceStore 的后台读取模式。
@MainActor
final class HistoryStore: ObservableObject {
    @Published var entries: [HistoryEntry] = []
    @Published var busy = false
    @Published var error: String?
    /// 区分「尚未加载」与「加载完成但为空」，空态文案据此选择。
    @Published var loaded = false

    func refresh() {
        guard !busy else { return }
        busy = true; error = nil
        Task {
            do { entries = try await Self.invoke(action: "list").entries ?? [] }
            catch { self.error = error.localizedDescription }
            busy = false; loaded = true
        }
    }

    /// 返回桥接原文结果（含「内容未变」的 saved: false）；失败（含 revision 冲突）以异常上抛，
    /// 由界面原文展示并重新拉取列表。成功与未变两种响应都附带最新 entries，直接覆盖本地状态。
    func save(id: String, revision: String, text: String) async throws -> HistoryResponse {
        // 请求体仅三个字段，桥接层按稳定 ID 重查原始记录，界面无法指定任意文件路径。
        let payload = try JSONEncoder().encode(["id": id, "revision": revision, "text": text])
        let response = try await Self.invoke(action: "save", stdin: payload)
        if let entries = response.entries { self.entries = entries }
        return response
    }

    /// 子进程输出在后台读取，界面不阻塞；与 ResourceStore 相同，合并 stdout/stderr 后取最后一行 JSON。
    private static func invoke(action: String, stdin payload: Data? = nil) async throws -> HistoryResponse {
        try await Task.detached(priority: .userInitiated) {
            guard let configURL = Bundle.main.url(forResource: "development-paths", withExtension: "json"),
                  let helper = Bundle.main.url(forResource: "dashboard_history", withExtension: "py") else {
                throw NSError(domain: "Dashboard", code: 1, userInfo: [NSLocalizedDescriptionKey: "请通过构建脚本生成开发 App 后运行。"])
            }
            let paths = try JSONDecoder().decode(DevelopmentPaths.self, from: Data(contentsOf: configURL))
            let process = Process()
            process.executableURL = URL(fileURLWithPath: paths.python)
            process.arguments = [helper.path, action, "--root", paths.root]
            let output = Pipe()
            // 合并输出后持续读取，防止桥接日志塞满 stderr 管道导致子进程挂起。
            process.standardOutput = output; process.standardError = output
            if let payload {
                let input = Pipe()
                process.standardInput = input
                try process.run()
                // save 请求体量极小，一次写入后立即关闭，不会撑满管道缓冲。
                try input.fileHandleForWriting.write(contentsOf: payload)
                input.fileHandleForWriting.closeFile()
            } else {
                try process.run()
            }
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let lines = String(decoding: data, as: UTF8.self).split(separator: "\n")
            guard let last = lines.last,
                  let response = try? JSONDecoder().decode(HistoryResponse.self, from: Data(last.utf8)) else {
                throw NSError(domain: "Dashboard", code: 2, userInfo: [NSLocalizedDescriptionKey: "历史桥接未返回有效结果。请检查开发环境。"])
            }
            if let message = response.error {
                throw NSError(domain: "Dashboard", code: 3, userInfo: [NSLocalizedDescriptionKey: message])
            }
            return response
        }.value
    }
}

/// 历史页只读浏览、搜索与复制；唯一的写入路径是「保存修订」。
/// 由主窗口详情区的 ScrollView 承载滚动，本视图只提供纵向内容。
struct HistoryView: View {
    @StateObject private var store = HistoryStore()
    @State private var query = ""
    @State private var expandedID: String?
    /// 编辑态与展开态分离：编辑锁住行折叠，revision 以进入编辑时的版本为准。
    @State private var editingID: String?
    @State private var editingRevision = ""
    @State private var draft = ""
    @State private var saving = false
    @State private var copiedID: String?
    /// 保存结果横幅：成功、内容未变与冲突错误共用一条通道，文案一律来自桥接原文。
    @State private var notice: String?
    @State private var noticeIsError = false

    /// 本地过滤，不重复触发桥接；文本内容与日期串任一命中即保留。
    private var filtered: [HistoryEntry] {
        let keyword = query.trimmingCharacters(in: .whitespaces)
        guard !keyword.isEmpty else { return store.entries }
        return store.entries.filter {
            $0.text.localizedCaseInsensitiveContains(keyword) || $0.date.contains(keyword)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            // 桥接层已截断到最近 200 条，更早的记录不展示、不参与搜索。
            Text("展示最近 200 条转录记录，搜索也在此范围内。修订会追加到标注库，原始记录保持不动。")
                .foregroundStyle(.secondary)
            toolbar
            banner
            if let error = store.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.orange).textSelection(.enabled)
            }
            content
        }
        .task { store.refresh() }
    }

    private var toolbar: some View {
        HStack(spacing: 12) {
            // 自绘搜索框而非 .searchable：页面位于 ScrollView 内，工具栏搜索体验不稳定。
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("搜索最近 200 条记录", text: $query).textFieldStyle(.plain)
                if !query.isEmpty {
                    Button { query = "" } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary)
                    }.buttonStyle(.plain).help("清空搜索")
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.8),
                        in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            Button { store.refresh() } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }.disabled(store.busy)
        }
    }

    @ViewBuilder private var banner: some View {
        if let notice {
            Label(notice, systemImage: noticeIsError ? "exclamationmark.triangle" : "checkmark.circle")
                .font(.callout)
                .foregroundStyle(noticeIsError ? Color.orange : Color.green)
                .textSelection(.enabled)
        }
    }

    @ViewBuilder private var content: some View {
        if !store.loaded && store.error == nil {
            ProgressView("正在读取转录历史…").padding(.vertical, 24)
        } else if store.entries.isEmpty && store.error == nil {
            VStack(spacing: 12) {
                Image(systemName: "text.quote")
                    .font(.system(size: 32, weight: .light)).foregroundStyle(.secondary)
                Text("暂无转录记录").font(.headline)
                Text("完成一次听写后，这里会出现转录记录。")
                    .font(.callout).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity).padding(32).modifier(ContentSurface())
        } else if filtered.isEmpty {
            Text("没有匹配「\(query)」的记录，换个关键词试试。")
                .font(.callout).foregroundStyle(.secondary)
        } else {
            // 行间节奏与概览页一致：行内 20 内边距，分隔线左右缩进。
            // 懒加载是硬要求：历史上限 200 条之外，长列表也不能一次性实例化全部行。
            LazyVStack(spacing: 0, pinnedViews: []) {
                ForEach(Array(filtered.enumerated()), id: \.element.id) { index, entry in
                    if index > 0 { Divider().padding(.horizontal, 20) }
                    row(entry)
                }
            }.modifier(ContentSurface())
        }
    }

    private func row(_ entry: HistoryEntry) -> some View {
        let expanded = expandedID == entry.id
        let editing = editingID == entry.id
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text(entry.date).font(.callout.monospacedDigit()).foregroundStyle(.secondary)
                badge(entry.source)
                if entry.corrected {
                    Label("已修订", systemImage: "checkmark.circle.fill")
                        .font(.caption).foregroundStyle(.green)
                }
                if entry.hasAudio {
                    // 界面拿不到音频路径，只展示存在性标记。
                    Image(systemName: "waveform").font(.caption).foregroundStyle(.secondary)
                        .help("该记录保留了音频")
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption).foregroundStyle(.tertiary)
                    .rotationEffect(.degrees(expanded ? 90 : 0))
            }
            // 收起态保持整行可点展开，展开态才允许选择文本，两种修饰类型不同需分支书写。
            if expanded {
                Text(entry.text)
                    .font(.body)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            } else {
                Text(entry.text)
                    .font(.body)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if expanded && !editing { detailActions(entry) }
            if editing { editor(entry) }
        }
        .padding(20)
        .contentShape(Rectangle())
        .onTapGesture {
            // 编辑态下不响应行折叠，避免误触丢失未保存草稿。
            guard !editing else { return }
            withAnimation(.easeInOut(duration: 0.15)) {
                expandedID = expanded ? nil : entry.id
            }
        }
    }

    /// 来源徽标用细胶囊区分两种历史来源，颜色仅作区分、不表达状态。
    private func badge(_ source: String) -> some View {
        let diary = source == "日记"
        return Text(source).font(.caption2.weight(.medium))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background((diary ? Color.accentColor : Color.purple).opacity(0.14), in: Capsule())
            .foregroundStyle(diary ? Color.accentColor : Color.purple)
    }

    private func detailActions(_ entry: HistoryEntry) -> some View {
        HStack(spacing: 16) {
            Button { copy(entry) } label: {
                Label(copiedID == entry.id ? "已复制" : "复制",
                      systemImage: copiedID == entry.id ? "checkmark" : "doc.on.doc")
            }
            Button("编辑") { beginEdit(entry) }
            Spacer()
        }
        .buttonStyle(.borderless).font(.callout)
    }

    private func editor(_ entry: HistoryEntry) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            TextEditor(text: $draft)
                .font(.body)
                .frame(minHeight: 96)
                .padding(4)
                .background(Color(nsColor: .textBackgroundColor),
                            in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(.primary.opacity(0.1))
                }
            HStack {
                Button(saving ? "保存中…" : "保存修订") { saveEdit() }
                    .buttonStyle(.borderedProminent).disabled(saving)
                Button("取消") { exitEdit() }.disabled(saving)
                Spacer()
            }
        }
    }

    /// 复制只写系统剪贴板，不触碰任何历史数据；成功反馈短暂显示后自动还原。
    private func copy(_ entry: HistoryEntry) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(entry.text, forType: .string)
        copiedID = entry.id
        Task {
            try? await Task.sleep(nanoseconds: 1_200_000_000)
            // 期间若复制了其他行，不覆盖其反馈状态。
            if copiedID == entry.id { copiedID = nil }
        }
    }

    private func beginEdit(_ entry: HistoryEntry) {
        editingID = entry.id
        editingRevision = entry.revision
        draft = entry.text
        notice = nil
    }

    /// 「取消」直接退出编辑态，不产生任何写入。
    private func exitEdit() {
        editingID = nil
        editingRevision = ""
        draft = ""
    }

    /// 保存结果一律以桥接原文展示；成功与「内容未变」都退出编辑态。
    /// 失败（如 revision 冲突）的响应不附带列表，需重新拉取以对齐真实状态。
    private func saveEdit() {
        saving = true
        Task {
            defer { saving = false }
            do {
                let response = try await store.save(id: editingID ?? "", revision: editingRevision, text: draft)
                notice = response.message ?? "已完成。"
                noticeIsError = false
                exitEdit()
            } catch {
                notice = error.localizedDescription
                noticeIsError = true
                exitEdit()
                store.refresh()
            }
        }
    }
}
