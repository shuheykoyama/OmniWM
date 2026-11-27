#!/usr/bin/env python3
"""Run a built x86_64 XCTest bundle with `xctest`, the way the pinned SwiftPM does.

SwiftPM needs its swiftpm-xctest-helper to list and select tests, and Xcode 27 ships that for arm64 only.
This follows its TestingSupport.constructTestEnvironment and its --filter / --skip handling.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time


# Private and nested test classes are named differently by XCTest and SwiftPM; the app has none, and
# one would stop the run here.
TEST_ID = re.compile(r"OmniWMTests\.\w+/\w+")
SWIFT_TESTING_IMPORT = re.compile(
    r"^[ \t]*(?:@\w+(?:\([^)\n]*\))?[ \t]+)*(?:\w+[ \t]+)?import[ \t]+(?:\w+[ \t]+)?Testing\b", re.MULTILINE)
# Mach-O section in which the compiler records every Swift Testing test.
SWIFT_TESTING_SECTION = "sectname __swift5_tests"


class TestRunError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise TestRunError(message)


def output(*command):
    return subprocess.run([str(part) for part in command], capture_output=True, text=True, check=True,
                          timeout=120).stdout.strip()


def developer_directory():
    return Path(output("xcrun", "--sdk", "macosx", "--show-sdk-platform-path")) / "Developer"


def executable(bundle):
    return bundle / "Contents/MacOS" / bundle.stem


def environment(base, bundle, developer, testing_path=None, interop=True):
    """The environment SwiftPM gives xctest; it lists tests without the interop mode."""
    env = dict(base)

    def append(key, value):
        env[key] = f"{env[key]}:{value}" if env.get(key) else str(value)

    # CI output is piped, never a terminal.
    env["NO_COLOR"] = "1"
    append("DYLD_LIBRARY_PATH", bundle.parent)
    if interop:
        # Default of a package with tools version 6.4 or later.
        env.setdefault("SWIFT_TESTING_XCTEST_INTEROP_MODE", "complete")
    if testing_path:
        # A swift.org toolchain keeps its Swift Testing libraries in usr/lib/swift/macosx/testing.
        append("DYLD_LIBRARY_PATH", testing_path)
    append("DYLD_FRAMEWORK_PATH", developer / "Library/Frameworks")
    append("DYLD_FRAMEWORK_PATH", developer / "Library/PrivateFrameworks")
    append("DYLD_LIBRARY_PATH", developer / "usr/lib")
    env["SWIFT_TESTING_ENABLED"] = "0"
    return env


def invocation(program, env, base, translated):
    """Command and environment to start. `arch` is protected, so DYLD variables have to be its arguments."""
    if not translated:
        return list(program), env
    added = [flag for key, value in env.items() if base.get(key) != value for flag in ("-e", f"{key}={value}")]
    return ["arch", "-x86_64", *added, *program], dict(base)


def exec_bytes(command, env):
    """What the kernel counts against ARG_MAX: every string, its terminator and its pointer."""
    strings = [*command, *[f"{key}={value}" for key, value in env.items()]]
    return sum(len(string.encode()) + 1 + 8 for string in strings)


def read_list(text):
    names = [line for line in text.splitlines() if line.strip()]
    require(names, "the test list is empty")
    unexpected = [name for name in names if not TEST_ID.fullmatch(name)]
    require(not unexpected, f"not test identifiers: {unexpected[:5]}")
    require(len(set(names)) == len(names), "the test list names a test twice")
    return names


def select(names, flag, pattern):
    """SwiftPM applies --filter and --skip as a regular expression search on "Module.Class/test"."""
    require(flag in ("--filter", "--skip"), f"unsupported flag {flag}")
    wanted = flag == "--filter"
    return [name for name in names if bool(re.search(pattern, name)) == wanted]


def partition(names, groups):
    """Every listed test must be selected by exactly one group."""
    selections = {group["name"]: select(names, group["flag"], group["pattern"]) for group in groups}
    empty = sorted(name for name, selected in selections.items() if not selected)
    require(not empty, f"groups that select no test: {empty}")
    counted = Counter(test for selected in selections.values() for test in selected)
    repeated = sorted(test for test, times in counted.items() if times > 1)
    require(not repeated, f"tests in more than one group: {repeated[:5]}")
    missing = sorted(set(names) - counted.keys())
    require(not missing, f"tests in no group: {missing[:5]}")
    return selections


def check_no_swift_testing(sources, load_commands):
    """Direct XCTest execution would leave Swift Testing tests out without a word."""
    importing = sorted(path.name for path in sources.rglob("*.swift")
                       if SWIFT_TESTING_IMPORT.search(path.read_text(encoding="utf-8", errors="replace")))
    require(not importing, f"Swift Testing is imported by {importing}; this runner only runs XCTest")
    require(SWIFT_TESTING_SECTION not in load_commands,
            "the bundle records Swift Testing tests; this runner only runs XCTest")


def runtime(developer):
    """Versions and architectures of what loads the bundle on this machine."""
    paths = {
        "xctest": Path(output("xcrun", "--sdk", "macosx", "--find", "xctest")),
        "XCTest": developer / "Library/Frameworks/XCTest.framework/XCTest",
        "Testing": developer / "Library/Frameworks/Testing.framework/Testing",
    }
    return {
        "machine": platform.machine(),
        "macos": platform.mac_ver()[0],
        "xcode": output("xcodebuild", "-version"),
        **{name: {"path": str(path), "architectures": output("lipo", "-archs", path).split()}
           for name, path in paths.items()},
    }


def check(args):
    names = read_list(args.list.read_text(encoding="utf-8"))
    check_no_swift_testing(args.tests_dir, output("xcrun", "otool", "-l", executable(args.bundle)))
    groups = json.loads(args.matrix)["include"]
    selections = partition(names, groups)
    limit = os.sysconf("SC_ARG_MAX")
    report = {"listed": len(names), "swift_testing": "none", "arg_max": limit, "groups": {
        name: {"tests": len(selected), "selection_bytes": len(",".join(selected).encode())}
        for name, selected in selections.items()}}
    print(json.dumps(report, indent=2, sort_keys=True))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


def list_bundle(args):
    base = dict(os.environ)
    env = environment(base, args.bundle, developer_directory(), args.testing_path, interop=False)
    command, env = invocation([str(args.lister), str(args.bundle)], env, base, args.translated)
    listed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=600)
    sys.stderr.write(listed.stderr)
    require(listed.returncode == 0, f"listing the tests failed with status {listed.returncode}")
    names = read_list(listed.stdout)
    args.output.write_text("".join(f"{name}\n" for name in names))
    print(f"{len(names)} tests listed")
    return 0


def run_group(args):
    developer = developer_directory()
    selected = select(read_list(args.list.read_text(encoding="utf-8")), args.flag, args.pattern)
    require(selected, "the group selects no test")
    found = runtime(developer)
    base = dict(os.environ)
    env = environment(base, args.bundle, developer, args.testing_path)
    program = [found["xctest"]["path"], "-XCTest", ",".join(selected), str(args.bundle)]
    command, env_to_start = invocation(program, env, base, args.translated)
    limit = os.sysconf("SC_ARG_MAX")
    size = exec_bytes(command, env_to_start)
    report = {
        **found,
        "translated": args.translated,
        "testing_path": str(args.testing_path) if args.testing_path else None,
        "added_environment": {key: value for key, value in env.items() if base.get(key) != value},
        "selected": len(selected),
        "exec_bytes": size,
        "arg_max": limit,
    }
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    require(size < limit, f"the selection needs {size} bytes of arguments and the limit is {limit}")
    started = time.monotonic()
    status = subprocess.run(command, env=env_to_start).returncode
    report.update(exit=status, seconds=round(time.monotonic() - started, 1))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if status < 0:
        print(f"xctest ended with signal {signal.Signals(-status).name}")
        return 128 - status
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)

    def command(name, action, summary):
        added = commands.add_parser(name, help=summary)
        added.set_defaults(action=action)
        added.add_argument("--bundle", type=Path, required=True)
        return added

    listing = command("list", list_bundle, "write the bundle's test identifiers")
    listing.add_argument("--lister", type=Path, required=True, help="compiled list_tests.swift")
    listing.add_argument("--output", type=Path, required=True)

    checking = command("check", check, "check the list against the test groups before running them")
    checking.add_argument("--list", type=Path, required=True)
    checking.add_argument("--matrix", required=True, help="JSON of Scripts/ci_test_plan.py")
    checking.add_argument("--tests-dir", type=Path, required=True)
    checking.add_argument("--report", type=Path, required=True)

    running = command("run", run_group, "run one test group")
    running.add_argument("--list", type=Path, required=True)
    running.add_argument("--flag", required=True)
    running.add_argument("--pattern", required=True)
    running.add_argument("--report", type=Path, required=True)

    for added in (listing, running):
        added.add_argument("--translated", action="store_true", help="run under Rosetta on an Apple silicon host")
        added.add_argument("--testing-path", type=Path, help="the pinned toolchain's Swift Testing libraries")
    args = parser.parse_args(argv)
    args.bundle = args.bundle.resolve()
    require(executable(args.bundle).is_file(), f"no test bundle at {args.bundle}")
    testing_path = getattr(args, "testing_path", None)
    require(testing_path is None or testing_path.is_dir(), f"no Swift Testing libraries at {testing_path}")
    return args.action(args)


if __name__ == "__main__":
    sys.exit(main())
