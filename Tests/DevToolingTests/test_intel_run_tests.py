import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load("intel_run_tests", "Scripts/intel/run_tests.py")
PLAN = load("intel_ci_test_plan", "Scripts/ci_test_plan.py")
BUNDLE = Path("/build/Debug/OmniWMTests.xctest")
DEVELOPER = Path("/platform/Developer")
ALPHA, BETA = "OmniWMTests.AlphaTests/testOne", "OmniWMTests.AlphaTestsMore/testTwo"


class IntelSwiftPMEnvironmentTests(unittest.TestCase):
    def test_search_paths_follow_the_order_of_swiftpm_and_keep_existing_values(self):
        env = RUNNER.environment({"DYLD_LIBRARY_PATH": "/mine", "HOME": "/home"}, BUNDLE, DEVELOPER, Path("/tc/testing"))
        self.assertEqual(env["DYLD_LIBRARY_PATH"], "/mine:/build/Debug:/tc/testing:/platform/Developer/usr/lib")
        self.assertEqual(env["DYLD_FRAMEWORK_PATH"],
                         "/platform/Developer/Library/Frameworks:/platform/Developer/Library/PrivateFrameworks")
        self.assertEqual((env["NO_COLOR"], env["SWIFT_TESTING_ENABLED"], env["HOME"]), ("1", "0", "/home"))
        self.assertEqual(env["SWIFT_TESTING_XCTEST_INTEROP_MODE"], "complete")

    def test_listing_has_no_interop_mode_and_a_machine_without_the_toolchain_no_testing_path(self):
        env = RUNNER.environment({}, BUNDLE, DEVELOPER, interop=False)
        self.assertNotIn("SWIFT_TESTING_XCTEST_INTEROP_MODE", env)
        self.assertEqual(env["DYLD_LIBRARY_PATH"], "/build/Debug:/platform/Developer/usr/lib")
        kept = RUNNER.environment({"SWIFT_TESTING_XCTEST_INTEROP_MODE": "none"}, BUNDLE, DEVELOPER)
        self.assertEqual(kept["SWIFT_TESTING_XCTEST_INTEROP_MODE"], "none")

    def test_translated_processes_receive_the_added_variables_through_arch(self):
        base = {"HOME": "/home"}
        env = {**base, "NO_COLOR": "1", "DYLD_LIBRARY_PATH": "/lib"}
        command, started_with = RUNNER.invocation(["/xctest", "bundle"], env, base, translated=True)
        self.assertEqual(command, ["arch", "-x86_64", "-e", "NO_COLOR=1", "-e", "DYLD_LIBRARY_PATH=/lib",
                                   "/xctest", "bundle"])
        self.assertEqual(started_with, base)
        self.assertEqual(RUNNER.invocation(["/xctest"], env, base, translated=False), (["/xctest"], env))


class IntelTestSelectionTests(unittest.TestCase):
    def test_filter_and_skip_search_the_identifier_as_swiftpm_does(self):
        pattern = PLAN.pattern(["AlphaTests"])
        self.assertEqual(RUNNER.select([ALPHA, BETA], "--filter", pattern), [ALPHA])
        self.assertEqual(RUNNER.select([ALPHA, BETA], "--skip", pattern), [BETA])
        with self.assertRaisesRegex(RuntimeError, "unsupported flag"):
            RUNNER.select([ALPHA], "--only", pattern)

    def test_upstream_groups_cover_every_test_exactly_once(self):
        # One test for every suite the plan knows, and one the source scan cannot know about.
        names = [f"OmniWMTests.{suite}/testOne" for suite in PLAN.test_cases()] + ["OmniWMTests.UnscannedTests/testOne"]
        groups = PLAN.full()
        selections = RUNNER.partition(names, groups)
        self.assertEqual(sorted(test for selected in selections.values() for test in selected), sorted(names))
        self.assertEqual(len(selections), len(groups))
        self.assertIn("OmniWMTests.UnscannedTests/testOne", selections[groups[-1]["name"]])

    def test_overlapping_missing_or_empty_groups_are_rejected(self):
        def group(name, flag, *suites):
            return {"name": name, "flag": flag, "pattern": PLAN.pattern(list(suites))}

        for groups, message in (
                ([group("a", "--filter", "AlphaTests"), group("b", "--skip", "Nothing")], "more than one group"),
                ([group("a", "--filter", "AlphaTests")], "in no group"),
                ([group("a", "--filter", "Nothing"), group("b", "--skip", "Nothing")], "select no test")):
            with self.assertRaisesRegex(RuntimeError, message):
                RUNNER.partition([ALPHA, BETA], groups)

    def test_list_must_hold_distinct_test_identifiers_only(self):
        self.assertEqual(RUNNER.read_list(f"{ALPHA}\n{BETA}\n"), [ALPHA, BETA])
        for text, message in (("", "empty"), (f"{ALPHA}\nBuilding...\n", "not test identifiers"),
                              ("_TtC4Mangled/testOne\n", "not test identifiers"), (f"{ALPHA}\n{ALPHA}\n", "twice")):
            with self.assertRaisesRegex(RuntimeError, message):
                RUNNER.read_list(text)

    def test_swift_testing_in_sources_or_in_the_bundle_stops_the_run(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "SomeTests.swift"
            source.write_text("import TestingSupport\n// import Testing\nimport XCTest\n")
            RUNNER.check_no_swift_testing(Path(raw), "sectname __text")
            with self.assertRaisesRegex(RuntimeError, "records Swift Testing tests"):
                RUNNER.check_no_swift_testing(Path(raw), "  sectname __swift5_tests\n")
            for line in ("import Testing", "  @testable import Testing", "@_spi(X) public import Testing",
                         "import struct Testing.Tag"):
                source.write_text(f"{line}\n")
                with self.assertRaisesRegex(RuntimeError, "SomeTests.swift", msg=line):
                    RUNNER.check_no_swift_testing(Path(raw), "")

    def test_this_repository_has_no_swift_testing_source(self):
        RUNNER.check_no_swift_testing(ROOT / "Tests", "")


class IntelTestRunTests(unittest.TestCase):
    def run_group(self, script, names=(ALPHA, BETA), limit=1_000_000):
        with tempfile.TemporaryDirectory(prefix="omniwm-intel-run-") as raw:
            root = Path(raw)
            bundle = root / "OmniWMTests.xctest"
            (bundle / "Contents/MacOS").mkdir(parents=True)
            (bundle / "Contents/MacOS/OmniWMTests").touch()
            xctest = root / "xctest"
            xctest.write_text(f"#!{sys.executable}\n{script}\n")
            xctest.chmod(0o755)
            (root / "list.txt").write_text("".join(f"{name}\n" for name in names))
            arguments = ["run", "--bundle", str(bundle), "--list", str(root / "list.txt"), "--flag=--filter",
                         f"--pattern={PLAN.pattern(['AlphaTests'])}", "--report", str(root / "reports/run.json")]
            output = io.StringIO()
            with patch.object(RUNNER, "developer_directory", return_value=DEVELOPER), \
                    patch.object(RUNNER, "runtime", return_value={"xctest": {"path": str(xctest)}}), \
                    patch.object(RUNNER.os, "sysconf", return_value=limit), contextlib.redirect_stdout(output):
                status = RUNNER.main(arguments)
            return status, json.loads((root / "reports/run.json").read_text()), root

    def test_xctest_gets_the_selection_and_its_exit_status_is_kept(self):
        script = ("import sys\nassert sys.argv[1:3] == ['-XCTest', 'OmniWMTests.AlphaTests/testOne'], sys.argv\n"
                  "assert sys.argv[3].endswith('OmniWMTests.xctest')\nsys.exit(0)")
        status, report, _ = self.run_group(script)
        self.assertEqual((status, report["exit"], report["selected"]), (0, 0, 1))
        self.assertEqual(report["added_environment"]["SWIFT_TESTING_ENABLED"], "0")
        self.assertEqual(self.run_group("import sys; sys.exit(1)")[0], 1)

    def test_crash_is_reported_as_the_signal(self):
        status, report, _ = self.run_group("import os, signal; os.kill(os.getpid(), signal.SIGTRAP)")
        self.assertEqual((status, report["exit"]), (133, -5))

    def test_selection_beyond_the_argument_limit_fails_before_starting(self):
        with self.assertRaisesRegex(RuntimeError, "bytes of arguments and the limit is 100"):
            self.run_group("raise SystemExit(0)", limit=100)

    def test_group_without_tests_does_not_pass(self):
        with self.assertRaisesRegex(RuntimeError, "selects no test"):
            self.run_group("raise SystemExit(0)", names=(BETA,))


if __name__ == "__main__":
    unittest.main()
