// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2026 OmniNull — https://github.com/OmniNull/OmniWM

import XCTest

final class CancellationTests: XCTestCase {
    // The property of a task value is inlined into the caller; on macOS 27 it calls a newer runtime function.
    func testTaskValueReportsCancellationOnlyAfterCancel() async {
        let (gate, opened) = AsyncStream.makeStream(of: Void.self)
        let task = Task<Void, Never> {
            for await _ in gate {}
        }
        XCTAssertFalse(task.isCancelled)
        task.cancel()
        XCTAssertTrue(task.isCancelled)
        await task.value
        XCTAssertTrue(task.isCancelled)
        opened.finish()
    }

    func testFinishedTaskThatWasNeverCancelledReportsFalse() async {
        let task = Task<Int, Never> { 1 }
        let value = await task.value
        XCTAssertEqual(value, 1)
        XCTAssertFalse(task.isCancelled)
    }

    func testRunningTaskSeesItsOwnCancellation() async {
        let (gate, opened) = AsyncStream.makeStream(of: Void.self)
        let (ready, started) = AsyncStream.makeStream(of: Void.self)
        let task = Task { () -> [Bool] in
            let before = [Task.isCancelled, withUnsafeCurrentTask { $0?.isCancelled ?? true }]
            started.finish()
            // Cancellation ends the iteration.
            for await _ in gate {}
            return before + [Task.isCancelled, withUnsafeCurrentTask { $0?.isCancelled ?? false }]
        }
        for await _ in ready {}
        task.cancel()
        let seen = await task.value
        XCTAssertEqual(seen, [false, false, true, true])
        opened.finish()
    }

    func testCancellationReachesChildTasksAndSleep() async {
        let task = Task { () -> [Bool] in
            await withTaskGroup(of: Bool.self) { group in
                group.addTask {
                    do {
                        try await Task.sleep(for: .seconds(60))
                        return false
                    } catch {
                        return error is CancellationError && Task.isCancelled
                    }
                }
                var seen: [Bool] = []
                for await child in group {
                    seen.append(child)
                }
                return seen
            }
        }
        task.cancel()
        let seen = await task.value
        XCTAssertEqual(seen, [true])
        XCTAssertTrue(task.isCancelled)
    }
}
