// swift-tools-version: 6.2
import PackageDescription

// Dashboard 只面向 macOS 26.0+ Apple Silicon；状态检查仍独立于窗口，便于
// 在不启动日常服务的情况下验证快照边界。
let package = Package(
    name: "CapsWriter",
    platforms: [.macOS(.v26)],
    products: [
        .library(name: "DashboardCore", targets: ["DashboardCore"]),
        .executable(name: "CapsWriterDashboard", targets: ["CapsWriterDashboard"]),
        .executable(name: "DashboardCoreChecks", targets: ["DashboardCoreChecks"])
    ],
    targets: [
        .target(name: "DashboardCore"),
        .executableTarget(name: "DashboardCoreChecks", dependencies: ["DashboardCore"]),
        .executableTarget(name: "CapsWriterDashboard", dependencies: ["DashboardCore"])
    ]
)
