import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPORT = load("intel_test_report", "Scripts/intel/test_report.py")
PRODUCTS = load("intel_verify_products", "Scripts/intel/verify_products.py")


def event(test, state, suite="OmniWMTests.AlphaTests"):
    timing = "." if state == "started" else " (0.001 seconds)."
    return f"Test Case '-[{suite} {test}]' {state}{timing}\n"


def passing(*tests):
    return "".join(event(test, "started") + event(test, "passed") for test in tests)


class IntelTestReportTests(unittest.TestCase):
    def report(self, *logs, **options):
        return REPORT.summarize({str(index): REPORT.read_log(log) for index, log in enumerate(logs)}, **options)

    def test_every_listed_test_passing_in_any_shard_passes(self):
        report = self.report(passing("testOne"), "Tests (shard 2)\tstep\t2026-11-27T00:00:00Z " + passing("testTwo"),
                             expected=["OmniWMTests.AlphaTests/testOne", "OmniWMTests.AlphaTests/testTwo"],
                             required=["OmniWMTests.AlphaTests"])
        self.assertTrue(report["passed_gate"], report["problems"])
        self.assertEqual(report["total"], {"tests": 2, "failed": 0, "unfinished": 0, "skipped": 0, "passed": 2})
        self.assertEqual(report["required"], {"OmniWMTests.AlphaTests": 2})

    def test_failure_skip_hang_and_absence_are_reported_separately(self):
        log = (passing("testOne") + event("testBad", "started") + event("testBad", "failed")
               + event("testSkip", "started") + event("testSkip", "skipped") + event("testHang", "started"))
        names = ("testOne", "testBad", "testSkip", "testHang", "testNever")
        report = self.report(log, expected=[f"OmniWMTests.AlphaTests/{name}" for name in names])
        self.assertFalse(report["passed_gate"])
        for key, name in (("failed", "testBad"), ("skipped", "testSkip"), ("unfinished", "testHang"),
                          ("not_run", "testNever")):
            self.assertEqual(report[key], [f"OmniWMTests.AlphaTests/{name}"], key)
        self.assertEqual(report["problems"], ["failed", "unfinished", "skipped", "not_run"])

    def test_repeated_test_counts_as_its_worst_result(self):
        report = self.report(passing("testOne"), event("testOne", "started") + event("testOne", "failed"))
        self.assertEqual(report["repeated"], ["OmniWMTests.AlphaTests/testOne"])
        self.assertEqual(report["total"]["failed"], 1)
        self.assertEqual(report["total"]["passed"], 0)

    def test_duplicate_runs_and_names_do_not_hide_behind_matching_counts(self):
        alpha, beta = "OmniWMTests.AlphaTests/testOne", "OmniWMTests.AlphaTests/testTwo"
        # Two results in one log and a test that never ran add up to the listed number.
        report = self.report(passing("testOne") + passing("testOne"), expected=[alpha, beta])
        self.assertEqual(report["problems"], ["not_run", "repeated"])
        report = self.report(passing("testOne"), expected=[alpha, alpha])
        self.assertEqual(report["problems"], ["listed_twice"])
        report = self.report(passing("testOne", "testOther"), expected=[alpha])
        self.assertEqual(report["problems"], ["unlisted"])

    def test_missing_required_suite_and_empty_log_do_not_pass(self):
        self.assertIn("no passing test in OmniWMTests.UpdaterTests",
                      self.report(passing("testOne"), required=["OmniWMTests.UpdaterTests"])["problems"])
        self.assertEqual(self.report("error: the bundle could not be loaded\n")["problems"], ["no test results"])

    def test_baseline_lists_the_tests_that_differ(self):
        report = self.report(passing("testOne", "testNew"),
                             expected=["OmniWMTests.AlphaTests/testOne", "OmniWMTests.AlphaTests/testNew"],
                             baseline={"base": REPORT.read_log(passing("testOne", "testGone"))})
        self.assertEqual(report["baseline"], {
            "tests": 2,
            "only_in_baseline": ["OmniWMTests.AlphaTests/testGone"],
            "only_here": ["OmniWMTests.AlphaTests/testNew"],
            "not_listed": ["OmniWMTests.AlphaTests/testGone"],
            "listed_only": ["OmniWMTests.AlphaTests/testNew"],
        })

    def test_command_line_writes_reports_and_fails_on_an_unrun_listed_test(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "rosetta-0.log").write_text(passing("testOne"))
            (root / "listed.txt").write_text("Building...\nOmniWMTests.AlphaTests/testOne\n"
                                             "OmniWMTests.AlphaTests/testTwo\n")
            arguments = [str(root / "rosetta-0.log"), "--expected", str(root / "listed.txt"),
                         "--output", str(root / "summary.json"), "--markdown", str(root / "summary.md")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(REPORT.main(arguments), 1)
            self.assertEqual(json.loads((root / "summary.json").read_text())["listed"], 2)
            self.assertIn("Not run: 1.", (root / "summary.md").read_text())
            (root / "listed.txt").write_text("Building...\n")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                REPORT.main(arguments)

    def test_workflow_requires_suites_that_exist(self):
        required = re.findall(r"--require OmniWMTests\.(\w+)", (ROOT / ".github/workflows/intel.yml").read_text())
        self.assertIn("AppSelfUpdaterTests", required)
        sources = "".join(path.read_text() for path in (ROOT / "Tests/OmniWMTests").glob("*.swift"))
        for suite in required:
            self.assertRegex(sources, rf"class {suite}\b")


@unittest.skipUnless(sys.platform == "darwin", "Uses Apple's clang, lipo and otool")
class IntelProductTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omniwm-intel-products-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.c").write_text("int main(void) { return 0; }\n")
        self.products = self.root / "Debug"
        self.bundle = self.products / "Probe.xctest/Contents/MacOS/Probe"
        self.bundle.parent.mkdir(parents=True)

    def compile(self, output, arch, *flags, minimum="26.0"):
        subprocess.run(["xcrun", "clang", "-arch", arch, f"-mmacosx-version-min={minimum}", str(self.root / "main.c"),
                        *flags, "-o", str(output)], check=True)

    def test_linked_x86_64_products_pass_and_object_files_are_ignored(self):
        self.compile(self.bundle, "x86_64")
        self.compile(self.products / "host.o", "arm64", "-c")
        binaries = PRODUCTS.verify(self.products)
        self.assertEqual(list(binaries), ["Probe.xctest/Contents/MacOS/Probe"])
        self.assertEqual(binaries["Probe.xctest/Contents/MacOS/Probe"]["outside_macos"], [])
        self.assertFalse(binaries["Probe.xctest/Contents/MacOS/Probe"]["newer_than_app_minimum"])

    def test_bundle_needing_a_newer_macos_is_recorded_for_the_investigation(self):
        self.compile(self.bundle, "x86_64", minimum="27.0")
        found = PRODUCTS.verify(self.products)["Probe.xctest/Contents/MacOS/Probe"]
        self.assertEqual(found["minimum_versions"], ["27.0"])
        self.assertTrue(found["newer_than_app_minimum"])

    def test_restored_products_must_be_the_built_ones_and_stay_untouched(self):
        self.compile(self.bundle, "x86_64")
        built = PRODUCTS.verify(self.products)
        marker = self.bundle.stat().st_mtime
        PRODUCTS.unchanged(self.products, PRODUCTS.verify(self.products), built, since=marker)
        os.utime(self.bundle, (marker + 5, marker + 5))
        with self.assertRaisesRegex(RuntimeError, "written again"):
            PRODUCTS.unchanged(self.products, PRODUCTS.verify(self.products), built, since=marker)
        (self.root / "main.c").write_text("int main(void) { return 1; }\n")
        self.compile(self.bundle, "x86_64")
        with self.assertRaisesRegex(RuntimeError, "differ from the build"):
            PRODUCTS.unchanged(self.products, PRODUCTS.verify(self.products), built)

    def test_arm64_product_or_missing_test_bundle_is_rejected(self):
        self.compile(self.products / "tool", "x86_64")
        with self.assertRaisesRegex(RuntimeError, "no XCTest bundle"):
            PRODUCTS.verify(self.products)
        self.compile(self.bundle, "arm64")
        with self.assertRaisesRegex(RuntimeError, "Probe is not x86_64-only"):
            PRODUCTS.verify(self.products)


@unittest.skipUnless(sys.platform == "darwin", "The script reads Apple toolchain output with jq")
class IntelRosettaScriptTests(unittest.TestCase):
    def run_script(self, host="arm64", translated="1", slices="x86_64 arm64"):
        with tempfile.TemporaryDirectory(prefix="omniwm-rosetta-") as raw:
            root = Path(raw)
            fakes = {
                "uname": f"echo {host}",
                "arch": ('case "$2" in /usr/sbin/sysctl) echo ' + translated + ';; /usr/bin/uname) echo x86_64;; '
                         '/usr/bin/true) ;; *) echo "translated: $2 $3";; esac'),
                "swift": 'echo \'{"paths":{"runtimeResourcePath":"/pinned.xctoolchain/usr/lib/swift"}}\'',
                "lipo": f'case "$2" in */swiftpm-xctest-helper) echo {slices};; *) echo x86_64 arm64;; esac',
                "xcrun": "echo /xctest",
                "sudo": 'echo "unexpected install" >&2; exit 9',
            }
            for name, body in fakes.items():
                (root / name).write_text(f"#!/bin/bash\n{body}\n")
                (root / name).chmod(0o755)
            exported = root / "env"
            result = subprocess.run(
                ["bash", str(ROOT / "Scripts/intel/rosetta.sh")], capture_output=True, text=True,
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "GITHUB_ENV": str(exported)},
            )
            return result, exported.read_text() if exported.exists() else ""

    def test_translated_host_exports_the_pinned_swift(self):
        result, exported = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(exported, "OMNIWM_X86_SWIFT=/pinned.xctoolchain/usr/bin/swift\n")
        self.assertIn("translated: /pinned.xctoolchain/usr/bin/swift --version", result.stdout)
        for tool in ("swift", "swift-package", "swiftpm-xctest-helper", "xctest"):
            self.assertIn(f"\n{tool} slices: x86_64 arm64", result.stdout)

    def test_tool_without_an_x86_64_slice_fails_before_the_tests(self):
        result, exported = self.run_script(slices="arm64")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("swiftpm-xctest-helper has no x86_64 slice", result.stderr)
        self.assertEqual(exported, "")

    def test_untranslated_or_intel_host_fails_without_exporting(self):
        for options, message in (({"translated": "0"}, "not running under Rosetta"),
                                 ({"host": "x86_64"}, "expected an Apple silicon host")):
            result, exported = self.run_script(**options)
            self.assertNotEqual(result.returncode, 0, message)
            self.assertIn(message, result.stderr)
            self.assertEqual(exported, "")


if __name__ == "__main__":
    unittest.main()
