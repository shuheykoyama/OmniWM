#!/usr/bin/env python3
"""Which private Core Animation setters that native glass requires does this macOS provide?

The requirements are taken from the enums of ShellGlassCapability.swift, and checked the way it checks them.
"""

import argparse
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "Sources/OmniWM/Core/Shell/ShellGlassCapability.swift"

PROGRAM = '''
_ = CALayer()
var requirements: [(String, [String])] = [
    ("CABackdropLayer", Capability.BackdropProperty.allCases.map(\\.rawValue)),
    ("CASDFLayer", Capability.BodyProperty.allCases.map(\\.rawValue)),
    ("CASDFElementLayer", Capability.ElementProperty.allCases.map(\\.rawValue))
]
requirements += Capability.Effect.allCases.map { ($0.rawValue, $0.keys.map(\\.rawValue)) }
var missing: [String] = []
var checked = 0
for (name, keys) in requirements {
    guard let type = NSClassFromString(name) as? NSObject.Type else {
        missing.append("\\(name).class")
        continue
    }
    for key in keys {
        checked += 1
        let setter = "set\\(key.prefix(1).uppercased())\\(key.dropFirst()):"
        if !type.instancesRespond(to: NSSelectorFromString(setter)) { missing.append("\\(name).\\(key)") }
    }
}
if NSClassFromString("CAFilter") == nil { missing.append("CAFilter.class") }
let report: [String: Any] = ["checked": checked, "missing": missing]
print(String(decoding: try JSONSerialization.data(withJSONObject: report), as: UTF8.self))
'''


def program(source):
    """The enums sit between the declaration of the capability and its first static property."""
    found = re.search(r"^struct ShellGlassCapability[^\n]*\{\n(.*?)^    static let sdfClassNames", source,
                      re.MULTILINE | re.DOTALL)
    if not found:
        raise RuntimeError("the capability's enums were not found")
    return f"import Foundation\nimport QuartzCore\nstruct Capability {{\n{found[1]}}}\n{PROGRAM}"


def probe():
    with tempfile.TemporaryDirectory(prefix="omniwm-glass-") as raw:
        code, binary = Path(raw) / "glass.swift", Path(raw) / "glass"
        code.write_text(program(SOURCE.read_text()))
        subprocess.run(["xcrun", "swiftc", "-swift-version", "5", str(code), "-o", str(binary)], check=True,
                       timeout=600)
        found = json.loads(subprocess.run([str(binary)], check=True, capture_output=True, text=True,
                                          timeout=120).stdout)
    return {"macos": platform.mac_ver()[0], "architecture": platform.machine(), **found}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    text = json.dumps(probe(), indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
