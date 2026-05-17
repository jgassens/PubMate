// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "PubMateSparkleSupport",
    platforms: [
        .macOS(.v13)
    ],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.9.1")
    ],
    targets: [
        .executableTarget(
            name: "PubMateSparkleSupport",
            dependencies: [
                .product(name: "Sparkle", package: "Sparkle")
            ]
        )
    ]
)
