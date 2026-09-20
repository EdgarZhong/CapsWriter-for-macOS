// swift-tools-version: 5.9
import PackageDescription

// 状态兼容层独立于窗口，便于在不启动日常服务的情况下验证迁移边界。
let package = Package(
    name: "CapsWriter",
    platforms: [.macOS(.v13)],
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
