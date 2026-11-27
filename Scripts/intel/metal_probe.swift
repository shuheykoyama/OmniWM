// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2026 OmniNull — https://github.com/OmniNull/OmniWM

import Foundation
import Metal
import QuartzCore

// A diagnosis, not a test: describes every Metal device, then repeats the way ShellLayerRaster reads a layer
// tree back (CARenderer into a texture, a marker command buffer on the same queue, getBytes) and counts what
// comes back, with shared and managed storage.

let size = 64
let iterations = 200
let space = CGColorSpace(name: CGColorSpace.sRGB)!
let colors: [[UInt8]] = [[0, 0, 255, 255], [255, 0, 0, 255]] // red and blue as stored BGRA

func color(_ index: Int) -> CGColor {
    let stored = colors[index]
    return CGColor(
        colorSpace: space,
        components: [CGFloat(stored[2]) / 255, CGFloat(stored[1]) / 255, CGFloat(stored[0]) / 255, 1]
    )!
}

// "solid" is one colored layer; "masked-gradient" is a gradient layer behind a mask, as the glass bodies use.
func makeContent(_ kind: String) -> CALayer {
    let frame = CGRect(x: 0, y: 0, width: size, height: size)
    if kind == "solid" {
        let layer = CALayer()
        layer.frame = frame
        return layer
    }
    let layer = CAGradientLayer()
    layer.frame = frame
    let mask = CALayer()
    mask.frame = frame
    mask.backgroundColor = CGColor(gray: 0, alpha: 1)
    layer.mask = mask
    return layer
}

func paint(_ layer: CALayer, _ index: Int) {
    if let gradient = layer as? CAGradientLayer {
        gradient.colors = [color(index), color(index)]
    } else {
        layer.backgroundColor = color(index)
    }
}

func makeRenderer(_ texture: any MTLTexture, _ queue: any MTLCommandQueue, _ root: CALayer) -> CARenderer {
    let renderer = CARenderer(
        mtlTexture: texture, options: [kCARendererMetalCommandQueue: queue, kCARendererColorSpace: space]
    )
    renderer.layer = root
    renderer.bounds = root.frame
    return renderer
}

// One frame as ShellLayerRaster encodes it; managed storage also needs the blit before the CPU reads.
func render(
    _ renderer: CARenderer, into texture: any MTLTexture, on queue: any MTLCommandQueue
) -> (any MTLCommandBuffer)? {
    let pass = MTLRenderPassDescriptor()
    pass.colorAttachments[0].texture = texture
    pass.colorAttachments[0].loadAction = .clear
    pass.colorAttachments[0].storeAction = .store
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0)
    guard let clear = queue.makeCommandBuffer(), let marker = queue.makeCommandBuffer(),
          let encoder = clear.makeRenderCommandEncoder(descriptor: pass)
    else { return nil }
    encoder.endEncoding()
    clear.commit()
    CATransaction.flush()
    renderer.beginFrame(atTime: CACurrentMediaTime(), timeStamp: nil)
    renderer.addUpdate(renderer.bounds)
    renderer.render()
    renderer.endFrame()
    if texture.storageMode == .managed, let blit = marker.makeBlitCommandEncoder() {
        blit.synchronize(resource: texture)
        blit.endEncoding()
    }
    marker.commit()
    marker.waitUntilCompleted()
    return marker
}

func readBack(_ texture: any MTLTexture, painted index: Int) -> String {
    var pixel = [UInt8](repeating: 0, count: 4)
    texture.getBytes(
        &pixel, bytesPerRow: size * 4, from: MTLRegionMake2D(size / 2, size / 2, 1, 1), mipmapLevel: 0
    )
    func near(_ wanted: [UInt8]) -> Bool {
        zip(pixel, wanted).allSatisfy { abs(Int($0) - Int($1)) <= 8 }
    }
    if near(colors[index % 2]) { return "expected" }
    if pixel == [0, 0, 0, 0] { return "empty" }
    return near(colors[(index + 1) % 2]) ? "previous" : "other"
}

func measure(_ device: any MTLDevice, kind: String, managed: Bool, fresh: Bool) -> [String: Any] {
    var counts = ["expected": 0, "empty": 0, "previous": 0, "other": 0, "failed": 0]
    var result: [String: Any] = ["content": kind, "storage": managed ? "managed" : "shared", "freshRenderer": fresh]
    let descriptor = MTLTextureDescriptor.texture2DDescriptor(
        pixelFormat: .bgra8Unorm, width: size, height: size, mipmapped: false
    )
    descriptor.usage = [.renderTarget, .shaderRead]
    descriptor.storageMode = managed ? .managed : .shared
    guard let queue = device.makeCommandQueue(), let texture = device.makeTexture(descriptor: descriptor) else {
        result["error"] = "no command queue or texture"
        return result
    }
    let root = CALayer()
    root.frame = CGRect(x: 0, y: 0, width: size, height: size)
    let content = makeContent(kind)
    root.addSublayer(content)
    var renderer = makeRenderer(texture, queue, root)
    for iteration in 0 ..< iterations {
        if fresh { renderer = makeRenderer(texture, queue, root) }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        paint(content, iteration % 2)
        CATransaction.commit()
        let marker = render(renderer, into: texture, on: queue)
        guard marker?.status == .completed else {
            counts["failed", default: 0] += 1
            result["error"] = marker?.error?.localizedDescription ?? "the command buffer did not complete"
            continue
        }
        counts[readBack(texture, painted: iteration), default: 0] += 1
    }
    result["counts"] = counts
    return result
}

func describe(_ device: any MTLDevice) -> [String: Any] {
    let families: [(String, MTLGPUFamily)] = [
        ("apple1", .apple1), ("apple2", .apple2), ("apple3", .apple3), ("apple4", .apple4), ("apple5", .apple5),
        ("apple6", .apple6), ("apple7", .apple7), ("apple8", .apple8), ("apple9", .apple9), ("mac2", .mac2),
        ("common1", .common1), ("common2", .common2), ("common3", .common3), ("metal3", .metal3)
    ]
    return [
        "name": device.name,
        "default": device.registryID == MTLCreateSystemDefaultDevice()?.registryID,
        "hasUnifiedMemory": device.hasUnifiedMemory,
        "isLowPower": device.isLowPower,
        "isHeadless": device.isHeadless,
        "isRemovable": device.isRemovable,
        "families": families.filter { device.supportsFamily($0.1) }.map(\.0)
    ]
}

var translated: Int32 = 0
var length = MemoryLayout<Int32>.size
// An Intel Mac has no such value, which reads as 0.
if sysctlbyname("sysctl.proc_translated", &translated, &length, nil, 0) != 0 { translated = 0 }

#if arch(x86_64)
    let architecture = "x86_64"
#else
    let architecture = "arm64"
#endif

let devices: [[String: Any]] = MTLCopyAllDevices().map { device in
    var report = describe(device)
    var runs: [[String: Any]] = []
    for kind in ["solid", "masked-gradient"] {
        for managed in [false, true] {
            for fresh in [false, true] {
                runs.append(measure(device, kind: kind, managed: managed, fresh: fresh))
            }
        }
    }
    report["readback"] = runs
    return report
}

let report: [String: Any] = [
    "architecture": architecture,
    "translated": translated == 1,
    "macOS": ProcessInfo.processInfo.operatingSystemVersionString,
    "iterations": iterations,
    "devices": devices
]
let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
print(String(decoding: data, as: UTF8.self))
