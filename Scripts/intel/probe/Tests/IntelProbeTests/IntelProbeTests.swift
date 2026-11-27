// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2026 OmniNull — https://github.com/OmniNull/OmniWM

import Foundation
import IntelProbe
import XCTest

final class IntelProbeTests: XCTestCase {
    func testRunsIntelCode() {
        #if arch(x86_64)
            let intel = true
        #else
            let intel = false
        #endif
        XCTAssertTrue(intel)

        // 1 under Rosetta; an Intel Mac has no such value, which reads as 0.
        var translated: Int32 = 0
        var size = MemoryLayout<Int32>.size
        let found = sysctlbyname("sysctl.proc_translated", &translated, &size, nil, 0) == 0
        print("probe: translated=\(found ? Int(translated) : 0)")
    }

    @MainActor
    func testAsyncWorkReturnsToTheMainActor() async {
        let value = await Task.detached { IntelProbe.double(21) }.value
        XCTAssertEqual(value, 42)
    }

    func testExpectationIsFulfilledFromAnotherQueue() {
        let fulfilled = expectation(description: "fulfilled")
        DispatchQueue.global().async { fulfilled.fulfill() }
        wait(for: [fulfilled], timeout: 30)
    }
}
