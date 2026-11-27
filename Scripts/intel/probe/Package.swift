// swift-tools-version: 6.5
import PackageDescription

let package = Package(
    name: "IntelProbe",
    platforms: [
        .macOS(.v26)
    ],
    targets: [
        .target(name: "IntelProbe"),
        .testTarget(name: "IntelProbeTests", dependencies: ["IntelProbe"])
    ]
)
