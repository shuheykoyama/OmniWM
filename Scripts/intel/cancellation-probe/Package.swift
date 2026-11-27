// swift-tools-version: 6.5
import PackageDescription

// Named like the app's test target, so that Scripts/intel/run_tests.py runs it unchanged.
let package = Package(
    name: "CancellationProbe",
    platforms: [
        .macOS(.v26)
    ],
    targets: [
        .testTarget(name: "OmniWMTests")
    ]
)
