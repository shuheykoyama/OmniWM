import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("intel_run_probe", ROOT / "Scripts/intel/probe/run_probe.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def run_output(tests, translated):
    lines = []
    for name in tests:
        suite, test = name.split("/")
        lines.append(f"Test Case '-[{suite} {test}]' started.")
        if test == "testRunsIntelCode":
            lines.append(f"probe: translated={translated}")
        lines.append(f"Test Case '-[{suite} {test}]' passed (0.001 seconds).")
    return "\n".join(lines)


class IntelProbeTests(unittest.TestCase):
    def attempt(self, body, translated=True, expected=PROBE.TESTS, timeout=60):
        with tempfile.TemporaryDirectory() as raw:
            return PROBE.attempt("probe", [sys.executable, "-c", body], expected, translated,
                                 Path(raw) / "attempt.log", timeout=timeout)

    def printing(self, text, status=0):
        return f"import sys; print({text!r}); sys.exit({status})"

    def test_all_expected_tests_passing_on_the_expected_machine_is_ok(self):
        found = self.attempt(self.printing(run_output(PROBE.TESTS, 1)))
        self.assertTrue(found["ok"], found["problems"])

    def test_loading_without_running_every_test_is_not_ok(self):
        for text, status, message in (
                ("The bundle couldn't be loaded.", 1, "exit status 1"),
                ("Executed 0 tests", 0, "got 0 of 0"),
                (run_output(PROBE.TESTS[:1], 1), 0, "got 1 of 1")):
            found = self.attempt(self.printing(text, status))
            self.assertFalse(found["ok"])
            self.assertIn(message, "; ".join(found["problems"]))

    def test_code_that_ran_natively_does_not_count_as_translated(self):
        found = self.attempt(self.printing(run_output(PROBE.TESTS, 0)))
        self.assertIn("expected translated=1", "; ".join(found["problems"]))
        self.assertTrue(self.attempt(self.printing(run_output(PROBE.TESTS, 0)), translated=False)["ok"])

    def test_hang_and_missing_program_are_recorded(self):
        found = self.attempt("import time; time.sleep(30)", timeout=1)
        self.assertIsNone(found["exit"])
        self.assertFalse(found["ok"])
        with tempfile.TemporaryDirectory() as raw:
            missing = PROBE.attempt("probe", [str(Path(raw) / "absent")], (), True, Path(raw) / "log")
        self.assertFalse(missing["ok"])

    def test_environment_reaches_a_translated_process_through_arch(self):
        translated = PROBE.command(["/xctest", "bundle"], True, {"NO_COLOR": "1"})
        self.assertEqual(translated, ["arch", "-x86_64", "-e", "NO_COLOR=1", "/xctest", "bundle"])
        native = PROBE.command(["/xctest", "bundle"], False, {"NO_COLOR": "1"})
        self.assertEqual(native, ["/usr/bin/env", "NO_COLOR=1", "/xctest", "bundle"])

    def test_probe_expects_the_tests_the_package_declares(self):
        source = (ROOT / "Scripts/intel/probe/Tests/IntelProbeTests/IntelProbeTests.swift").read_text()
        declared = {f"{PROBE.SUITE}/{line.split('func ')[1].split('(')[0]}"
                    for line in source.splitlines() if "func test" in line}
        self.assertEqual(declared, set(PROBE.TESTS))


if __name__ == "__main__":
    unittest.main()
