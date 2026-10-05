// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AIUsage",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "AIUsage", targets: ["AIUsageApp"]),
    ],
    targets: [
        .target(
            name: "AIUsageCore",
            resources: [.copy("Resources/fixtures")]
        ),
        .target(
            name: "AIUsageDesign",
            dependencies: ["AIUsageCore"]
        ),
        .executableTarget(
            name: "AIUsageApp",
            dependencies: ["AIUsageCore", "AIUsageDesign"]
        ),
        .testTarget(
            name: "AIUsageCoreTests",
            dependencies: ["AIUsageCore"]
        ),
    ]
)
