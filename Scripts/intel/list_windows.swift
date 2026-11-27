// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2026 OmniNull — https://github.com/OmniNull/OmniWM

import CoreGraphics
import Foundation

// Prints the on-screen windows of one process. Owner, layer and bounds need no permission;
// titles stay empty without Screen Recording, which is never requested here.
let arguments = CommandLine.arguments
guard arguments.count == 2, let target = Int(arguments[1]) else {
    print("usage: list_windows <pid>")
    exit(2)
}

let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
let owned = windows.filter { $0[kCGWindowOwnerPID as String] as? Int == target }
print("windows=\(owned.count)")
for window in owned {
    let layer = window[kCGWindowLayer as String] as? Int ?? 0
    let bounds = window[kCGWindowBounds as String] as? [String: Double] ?? [:]
    let name = window[kCGWindowName as String] as? String ?? ""
    print("layer=\(layer) width=\(bounds["Width"] ?? 0) height=\(bounds["Height"] ?? 0) name=\(name)")
}
