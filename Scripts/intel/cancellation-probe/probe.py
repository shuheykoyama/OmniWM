#!/usr/bin/env python3
"""Does weak-linking libswift_Concurrency let the pinned toolchain's x86_64 tests run on macOS 26?

Task.isCancelled on a task value is inlined into the caller, and its macOS 27 branch imports
swift_task_isCancelledWithFlags. Build the same tests with and without weak linking, compare what each
binary imports, and run them.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("intel_run_tests", ROOT / "Scripts/intel/run_tests.py")
report_tool = load("intel_test_report", ROOT / "Scripts/intel/test_report.py")

LIBRARY = "libswift_Concurrency"
TARGET = "_swift_task_isCancelledWithFlags"
# Extra `swift build` arguments of each variant; {sdk} is the macOS SDK.
VARIANTS = {
    "baseline": [],
    "weak-l": ["-Xlinker", "-weak-lswift_Concurrency"],
    "weak-library": ["-Xlinker", "-weak_library", "-Xlinker", "{sdk}/usr/lib/swift/libswift_Concurrency.tbd"],
}
IMPORT = re.compile(r"^\s+0x[0-9A-Fa-f]+\s+(\S+)( \[weak-import\])?\s+\(from ([^)]+)\)$", re.MULTILINE)
LOAD = re.compile(r"cmd (LC_LOAD\w*DYLIB)\n\s*cmdsize \d+\n\s*name (\S+)")
INTERFACE = "_Concurrency.swiftmodule/x86_64-apple-macos.swiftinterface"
DECLARATIONS = re.compile(r"func _taskIsCancelledWithFlags\b|var isCancelled: Swift\.Bool")


def run(*command, **options):
    return subprocess.run([str(part) for part in command], capture_output=True, text=True, check=True,
                          timeout=900, **options).stdout


def imports(text):
    """Map (library, symbol) to whether the import is weak."""
    return {(library, symbol): bool(weak) for symbol, weak, library in IMPORT.findall(text)}


def callers(disassembly):
    """Functions that reference the target symbol, with whether they test the OS version before it."""
    found, name, guarded = {}, None, False
    for line in disassembly.splitlines():
        if line.endswith(":") and not line[0].isspace() and "\t" not in line:
            name, guarded = line[:-1], False
        elif "isOSVersionAtLeast" in line or "isPlatformVersionAtLeast" in line:
            guarded = True
        elif TARGET in line:
            found[name] = guarded
    return found


def compare(baseline, variant):
    """What weak linking changed: attributes of imports present in both, and imports in only one."""
    shared = baseline.keys() & variant.keys()
    return {
        "newly_weak": sorted(f"{library} {symbol}" for library, symbol in shared
                             if variant[library, symbol] and not baseline[library, symbol]),
        "no_longer_weak": sorted(f"{library} {symbol}" for library, symbol in shared
                                 if baseline[library, symbol] and not variant[library, symbol]),
        "only_in_baseline": sorted(f"{library} {symbol}" for library, symbol in baseline.keys() - variant.keys()),
        "only_in_variant": sorted(f"{library} {symbol}" for library, symbol in variant.keys() - baseline.keys()),
    }


def declarations(interface):
    """The declarations of interest, each with the attribute lines above it."""
    lines = interface.splitlines()
    found = []
    for index, line in enumerate(lines):
        if DECLARATIONS.search(line):
            start = index
            while start and lines[start - 1].lstrip().startswith("@"):
                start -= 1
            # A transparent getter prints its body, which holds the availability test.
            end = index + 1
            if "isCancelled" in line and "{" in line:
                depth = 0
                for end in range(index, min(index + 20, len(lines))):
                    depth += lines[end].count("{") - lines[end].count("}")
                    if depth <= 0:
                        break
                end += 1
            found.append("\n".join(lines[start:end]))
    return found


def bundle(scratch):
    return scratch / "out/Products/Debug/OmniWMTests.xctest"


def inspect(binary):
    found = imports(run("xcrun", "dyld_info", "-imports", binary))
    loads = {Path(name).name.removesuffix(".dylib"): command
             for command, name in LOAD.findall(run("xcrun", "otool", "-l", binary))}
    target = {library: weak for (library, symbol), weak in found.items() if symbol == TARGET}
    summary = {}
    for (library, _), weak in found.items():
        counts = summary.setdefault(library, {"strong": 0, "weak": 0})
        counts["weak" if weak else "strong"] += 1
    return found, {
        "load_command": loads.get(LIBRARY),
        "target_import": "weak" if target.get(LIBRARY) else "strong" if LIBRARY in target else "absent",
        "imports": summary,
        "callers": callers(run("xcrun", "otool", "-tV", binary)),
    }


def toolchain(swift):
    """Which _Concurrency interface the compiler loads, and what the candidates declare."""
    sdk = run("xcrun", "--sdk", "macosx", "--show-sdk-path").strip()
    swiftc = swift.parent / "swiftc"
    resources = Path(json.loads(run(swiftc, "-print-target-info"))["paths"]["runtimeResourcePath"])
    loading = subprocess.run(
        [str(swiftc), "-typecheck", "-Rmodule-loading", "-target", "x86_64-apple-macos26.0", "-sdk", sdk, "-"],
        input="import _Concurrency\n", capture_output=True, text=True, timeout=300).stderr
    candidates = [*resources.glob(f"macosx/{INTERFACE}"), Path(sdk) / "usr/lib/swift" / INTERFACE]
    return sdk, {
        "loaded": [line for line in loading.splitlines() if "loaded module '_Concurrency'" in line],
        "interfaces": {str(path): declarations(path.read_text()) for path in candidates if path.is_file()},
    }


def build(args):
    sdk, report = toolchain(args.swift)
    report["variants"], found = {}, {}
    for name, flags in VARIANTS.items():
        scratch = args.scratch / name
        subprocess.run([str(args.swift), "build", "--package-path", str(PACKAGE), "--build-tests",
                        "--build-system", "swiftbuild", "--arch", "x86_64", "--scratch-path", str(scratch),
                        *[flag.format(sdk=sdk) for flag in flags]], check=True, timeout=1200)
        binary = runner.executable(bundle(scratch))
        found[name], report["variants"][name] = inspect(binary)
        report["variants"][name]["flags"] = flags
    for name in VARIANTS:
        if name != "baseline":
            report["variants"][name]["compared_with_baseline"] = compare(found["baseline"], found[name])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def test_names(source=PACKAGE / "Tests/OmniWMTests/CancellationTests.swift"):
    return [f"OmniWMTests.CancellationTests/{name}" for name in re.findall(r"func (test\w+)", source.read_text())]


def outcome(name, status, log, expected):
    results = report_tool.read_log(log)
    passed = sorted(test for test, found in results.items() if found and report_tool.state(found) == "passed")
    missing = re.findall(r"Symbol not found: (\S+)", log)
    return {"variant": name, "exit": status, "passed": passed, "missing_symbol": sorted(set(missing)),
            "loaded": bool(results), "all_passed": status == 0 and passed == sorted(expected)}


def execute(args):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    expected = test_names()
    listing = args.output_dir / "tests.txt"
    listing.write_text("".join(f"{name}\n" for name in expected))
    outcomes = []
    for name in VARIANTS:
        command = [sys.executable, str(ROOT / "Scripts/intel/run_tests.py"), "run",
                   "--bundle", str(bundle(args.scratch / name)), "--list", str(listing),
                   "--flag=--filter", r"--pattern=OmniWMTests\.", "--report", str(args.output_dir / f"{name}.json")]
        if args.translated:
            command.append("--translated")
        if args.testing_path:
            command += ["--testing-path", str(args.testing_path)]
        finished = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=300)
        log = finished.stdout + finished.stderr
        (args.output_dir / f"{name}.log").write_text(log)
        outcomes.append(outcome(name, finished.returncode, log, expected))
    (args.output_dir / "results.json").write_text(json.dumps(outcomes, indent=2) + "\n")
    print(json.dumps(outcomes, indent=2))
    failed = [found["variant"] for found in outcomes
              if not found["all_passed"] and found["variant"] not in args.may_fail]
    if failed:
        print(f"::error::variants that did not pass: {', '.join(failed)}")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    building = commands.add_parser("build", help="build every variant and inspect its imports")
    building.set_defaults(action=build)
    building.add_argument("--swift", type=Path, required=True)
    building.add_argument("--report", type=Path, required=True)
    running = commands.add_parser("run", help="run every variant on this machine")
    running.set_defaults(action=execute)
    running.add_argument("--output-dir", type=Path, required=True)
    running.add_argument("--translated", action="store_true")
    running.add_argument("--testing-path", type=Path)
    running.add_argument("--may-fail", action="append", default=[], help="variant whose failure is recorded only")
    for added in (building, running):
        added.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args(argv)
    args.scratch = args.scratch.resolve()
    return args.action(args)


if __name__ == "__main__":
    sys.exit(main())
