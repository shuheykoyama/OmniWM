// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2026 OmniNull — https://github.com/OmniNull/OmniWM

import Foundation
import XCTest

// Prints "Module.Class/test" for every test of a bundle, the identifiers `xctest -XCTest` accepts.
// It replaces SwiftPM's swiftpm-xctest-helper, which Xcode 27 ships for arm64 only.
func list(_ test: XCTest) {
    if let suite = test as? XCTestSuite {
        suite.tests.forEach(list)
        return
    }
    // A test case is named "-[Class test]".
    guard let method = test.name.dropLast().split(separator: " ").last else {
        fail("unexpected test name: \(test.name)")
    }
    print("\(NSStringFromClass(type(of: test)))/\(method)")
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("list_tests: \(message)\n".utf8))
    exit(1)
}

guard CommandLine.arguments.count == 2, let bundle = Bundle(path: CommandLine.arguments[1]) else {
    fail("usage: list_tests <bundle.xctest>")
}

do {
    try bundle.loadAndReturnError()
} catch {
    fail("\(error)")
}

list(XCTestSuite.default)
