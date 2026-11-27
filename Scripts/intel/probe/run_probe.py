#!/usr/bin/env python3
"""Can this machine load an x86_64 XCTest bundle and run its tests? Record the tools and try."""

import argparse
import importlib.util
import json
from pathlib import Path
import platform
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location("intel_test_report", ROOT / "Scripts/intel/test_report.py")
report_tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(report_tool)

SUITE = "IntelProbeTests.IntelProbeTests"
TESTS = tuple(f"{SUITE}/{name}" for name in (
    "testRunsIntelCode", "testAsyncWorkReturnsToTheMainActor", "testExpectationIsFulfilledFromAnotherQueue"))
SELECTED = TESTS[-1]
TRANSLATED = re.compile(r"probe: translated=(\d)")


def output(*command):
    return subprocess.run(command, capture_output=True, text=True, timeout=120).stdout.strip()


def developer_directory():
    return Path(output("xcrun", "--sdk", "macosx", "--show-sdk-platform-path")) / "Developer"


def architectures(paths):
    found = {}
    for label, path in paths.items():
        path = Path(path)
        slices = output("lipo", "-archs", str(path)) if path.exists() else ""
        found[label] = {"path": str(path), "architectures": slices.split() if slices else None}
    return found


def tools(bundle, swift=None):
    developer = developer_directory()
    xcode_helper = Path(output("xcrun", "--find", "swift-build")).parent.parent / "libexec/swift/pm/swiftpm-xctest-helper"
    paths = {
        "xctest": output("xcrun", "--find", "xctest"),
        "XCTest.framework": developer / "Library/Frameworks/XCTest.framework/XCTest",
        "XCTestCore.framework": developer / "Library/PrivateFrameworks/XCTestCore.framework/XCTestCore",
        "XCTestSupport.framework": developer / "Library/PrivateFrameworks/XCTestSupport.framework/XCTestSupport",
        "XCUnit.framework": developer / "Library/PrivateFrameworks/XCUnit.framework/XCUnit",
        "XCTAutomationSupport.framework":
            developer / "Library/PrivateFrameworks/XCTAutomationSupport.framework/XCTAutomationSupport",
        "libXCTestSwiftSupport.dylib": developer / "usr/lib/libXCTestSwiftSupport.dylib",
        "Xcode swiftpm-xctest-helper": xcode_helper,
        "test bundle": bundle / "Contents/MacOS" / bundle.stem,
    }
    if swift:
        for name in ("swift", "swift-test", "swift-package"):
            paths[f"toolchain {name}"] = swift.parent / name
        paths["toolchain swiftpm-xctest-helper"] = swift.parent.parent / "libexec/swift/pm/swiftpm-xctest-helper"
    return architectures(paths)


def swiftpm_environment(developer):
    """What the pinned SwiftPM adds on macOS before it starts xctest."""
    return {
        "NO_COLOR": "1",
        "SWIFT_TESTING_ENABLED": "0",
        "DYLD_FRAMEWORK_PATH": str(developer / "Library/Frameworks"),
        "DYLD_LIBRARY_PATH": str(developer / "usr/lib"),
    }


def command(program, translated, environment=None):
    """Under Rosetta, `arch` also carries the environment past the protections that strip DYLD variables."""
    variables = [f"{key}={value}" for key, value in (environment or {}).items()]
    if translated:
        return ["arch", "-x86_64", *[flag for variable in variables for flag in ("-e", variable)], *program]
    return ["/usr/bin/env", *variables, *program]


def attempt(name, arguments, expected, translated, log, timeout=600):
    try:
        run = subprocess.run(arguments, capture_output=True, text=True, errors="replace", timeout=timeout)
        status, text = run.returncode, run.stdout + run.stderr
    except subprocess.TimeoutExpired as error:
        status, text = None, f"timed out after {timeout} seconds\n{error.stdout or ''}"
    except OSError as error:
        status, text = None, f"could not start: {error}"
    log.write_text(f"$ {' '.join(arguments)}\n{text}")
    results = report_tool.read_log(text)
    passed = sorted(test for test, found in results.items() if found and report_tool.state(found) == "passed")
    flags = sorted(set(TRANSLATED.findall(text)))
    problems = []
    if status != 0:
        problems.append(f"exit status {status}")
    if expected:
        if passed != sorted(expected) or len(results) != len(expected):
            problems.append(f"expected {len(expected)} passing tests, got {len(passed)} of {len(results)}")
        if f"{SUITE}/testRunsIntelCode" in expected and flags != [str(int(translated))]:
            problems.append(f"expected translated={int(translated)}, the test reported {flags}")
    return {"name": name, "command": arguments, "exit": status, "passed_tests": passed, "problems": problems,
            "ok": not problems, "tail": text.splitlines()[-25:]}


def probe(bundle, output_directory, translated, swift=None, package=None, scratch=None):
    output_directory.mkdir(parents=True, exist_ok=True)
    developer = developer_directory()
    xctest = output("xcrun", "--find", "xctest")
    environment = swiftpm_environment(developer)
    plans = [
        ("direct", command([xctest, str(bundle)], translated), TESTS),
        ("direct with SwiftPM environment", command([xctest, str(bundle)], translated, environment), TESTS),
        ("direct selection", command([xctest, "-XCTest", SELECTED, str(bundle)], translated, environment),
         (SELECTED,)),
    ]
    if swift:
        # Supplementary: SwiftPM itself, which needs no listing helper when nothing is filtered.
        options = ["--skip-build", "--build-system", "swiftbuild", "--arch", "x86_64",
                   "--package-path", str(package), "--scratch-path", str(scratch)]
        plans += [
            ("swift test unfiltered", command([str(swift), "test", *options], translated), TESTS),
            ("swift test list", command([str(swift), "test", "list", *options], translated), ()),
        ]
    attempts = [attempt(name, arguments, expected, translated, output_directory / f"{index}.log")
                for index, (name, arguments, expected) in enumerate(plans)]
    wanted = {"direct with SwiftPM environment", "direct selection"}
    return {
        "machine": platform.machine(),
        "macos": platform.mac_ver()[0],
        "xcode": output("xcodebuild", "-version"),
        "translated": translated,
        "tools": tools(bundle, swift),
        "attempts": attempts,
        # The method under consideration works only if it loads the bundle and selects tests.
        "direct_execution_works": all(found["ok"] for found in attempts if found["name"] in wanted),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--translated", action="store_true", help="run under Rosetta on an Apple silicon host")
    parser.add_argument("--swift", type=Path, help="pinned toolchain's swift, to also try `swift test`")
    parser.add_argument("--package-path", type=Path)
    parser.add_argument("--scratch-path", type=Path)
    args = parser.parse_args(argv)
    found = probe(args.bundle.resolve(), args.output_dir.resolve(), args.translated, args.swift,
                  args.package_path, args.scratch_path)
    (args.output_dir / "probe.json").write_text(json.dumps(found, indent=2, sort_keys=True) + "\n")
    for label, tool in found["tools"].items():
        print(f"{label}: {' '.join(tool['architectures']) if tool['architectures'] else 'absent'} ({tool['path']})")
    for entry in found["attempts"]:
        print(f"\n== {entry['name']}: {'ok' if entry['ok'] else '; '.join(entry['problems'])}")
        if not entry["ok"]:
            print("\n".join(entry["tail"]))
    print(f"\ndirect execution works: {found['direct_execution_works']}")
    return 0 if found["direct_execution_works"] else 1


if __name__ == "__main__":
    sys.exit(main())
