import AppKit
import Foundation

/// 开发桥接的路径由构建脚本提供；不从窗口中猜测仓库位置。
private struct DevelopmentPaths: Decodable {
    let root: String
    let python: String
}

struct InferenceResource: Decodable, Identifiable {
    let id: String
    let directory: String
    let modelReady: Bool
    let runtimeReady: Bool
    let warning: String?
    var ready: Bool { modelReady && runtimeReady }
    var title: String { "Qwen3-ASR 1.7B" }
    var precision: String { id == "8bit" ? "8-bit" : "4-bit" }
    var status: String {
        if ready { return warning == nil ? "资源齐备" : "资源齐备，规格需核对" }
        if !runtimeReady && !modelReady { return "待下载模型与运行时" }
        return modelReady ? "待安装运行时" : "模型缺失或不完整"
    }
}

private struct ResourceResponse: Decodable {
    let plans: [InferenceResource]?
    let error: String?
}

/// 子进程输出在后台读取，界面不阻塞；刷新与下载互斥，避免重复覆盖资源。
@MainActor
final class ResourceStore: ObservableObject {
    @Published var plans: [InferenceResource] = []
    @Published var busy = false
    @Published var downloading: String?
    @Published var error: String?
    @Published var lastChecked: Date?

    func startClient() { perform(action: "start", variant: nil) }
    func refresh() { perform(action: "inspect", variant: nil) }
    func download(_ resource: InferenceResource) { perform(action: "download", variant: resource.id) }

    func openDirectory(_ resource: InferenceResource) {
        do {
            let url = URL(fileURLWithPath: resource.directory, isDirectory: true)
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            if !NSWorkspace.shared.open(url) { error = "无法打开资源目录。" }
        } catch { self.error = error.localizedDescription }
    }

    private func perform(action: String, variant: String?) {
        guard !busy else { return }
        busy = true; error = nil; downloading = variant
        Task {
            do {
                let response = try await Task.detached(priority: .userInitiated) {
                    guard let configURL = Bundle.main.url(forResource: "development-paths", withExtension: "json"),
                          let helper = Bundle.main.url(forResource: "dashboard_resources", withExtension: "py") else {
                        throw NSError(domain: "Dashboard", code: 1, userInfo: [NSLocalizedDescriptionKey: "请通过构建脚本生成开发 App 后运行。"])
                    }
                    let paths = try JSONDecoder().decode(DevelopmentPaths.self, from: Data(contentsOf: configURL))
                    let process = Process()
                    process.executableURL = URL(fileURLWithPath: paths.python)
                    process.arguments = [helper.path, action, "--root", paths.root]
                    if let variant { process.arguments! += ["--variant", variant] }
                    let pipe = Pipe()
                    // 合并输出后持续读取，防止依赖安装或下载日志塞满 stderr 管道。
                    process.standardOutput = pipe; process.standardError = pipe
                    try process.run()
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    let lines = String(decoding: data, as: UTF8.self).split(separator: "\n")
                    guard let last = lines.last,
                          let response = try? JSONDecoder().decode(ResourceResponse.self, from: Data(last.utf8)) else {
                        throw NSError(domain: "Dashboard", code: 2, userInfo: [NSLocalizedDescriptionKey: "资源检测未返回有效结果。请检查开发环境。"])
                    }
                    if let message = response.error {
                        throw NSError(domain: "Dashboard", code: 3, userInfo: [NSLocalizedDescriptionKey: message])
                    }
                    guard process.terminationStatus == 0, response.plans != nil else {
                        throw NSError(domain: "Dashboard", code: 4, userInfo: [NSLocalizedDescriptionKey: "资源操作未完成，请重试。"])
                    }
                    return response
                }.value
                plans = response.plans ?? []; lastChecked = Date()
            } catch { self.error = error.localizedDescription }
            busy = false; downloading = nil
        }
    }
}
