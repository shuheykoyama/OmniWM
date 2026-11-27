import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("intel_smoke_app", ROOT / "Scripts/intel/smoke_app.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)

# Stand-ins for the app binaries. Only "direct" launches run here, so nothing is registered with the system.
SOURCE = """#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--help") == 0) { puts("Usage: omniwmctl <command>"); return HELP_STATUS; }
    fputs(MESSAGE, stderr);
    if (IGNORE_TERM) signal(SIGTERM, SIG_IGN);
    if (SPAWN_HELPER && fork() == 0) { execl(HELPER, HELPER, "child", (char *)NULL); _exit(9); }
    sleep(LIFETIME);
    return 3;
}
"""


@unittest.skipUnless(sys.platform == "darwin", "Compiles small macOS programs to stand in for the app")
class IntelSmokeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omniwm-smoke-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "app.c").write_text(SOURCE)
        self.app = self.root / "OmniWM.app"
        self.output = self.root / "reports"
        self.output.mkdir()
        self.crashes = self.root / "crashes"
        self.crashes.mkdir()
        (self.app / "Contents/MacOS").mkdir(parents=True)
        self.helper = self.app / "Contents/MacOS/helper"
        self.build(self.app / "Contents/MacOS/omniwmctl")

    def build(self, output, lifetime=30, message="", ignore_term=0, spawn_helper=0, help_status=0):
        definitions = {
            "LIFETIME": lifetime, "MESSAGE": json.dumps(message), "IGNORE_TERM": ignore_term,
            "SPAWN_HELPER": spawn_helper, "HELP_STATUS": help_status, "HELPER": json.dumps(str(self.helper)),
        }
        subprocess.run(["xcrun", "clang", str(self.root / "app.c"), "-o", str(output),
                        *[f"-D{key}={value}" for key, value in definitions.items()]], check=True)

    def launch(self, **options):
        self.build(self.app / "Contents/MacOS/OmniWM", **options)
        found = SMOKE.launch(self.app, "direct", self.output, seconds=2, grace=2,
                             crash_directories=(self.crashes,), report_delay=0)
        self.assertEqual(SMOKE.referencing(self.app), {}, "the launch left processes behind")
        return found

    def test_app_that_stays_up_and_ends_on_request_passes(self):
        found = self.launch()
        self.assertEqual(found["problems"], [])
        self.assertEqual(found["alive_after_seconds"], 2)
        self.assertEqual(found["stopped"], "terminated")

    def test_early_exit_and_loader_failure_are_reported(self):
        found = self.launch(lifetime=0, message="dyld[1]: Symbol not found: _missing\n")
        self.assertIn("exited within 2 seconds (status 3)", found["problems"][0])
        self.assertEqual(found["dyld_failures"], ["dyld[1]: Symbol not found: _missing"])
        self.assertIn("the dynamic loader reported a failure", found["problems"])

    def test_app_ignoring_the_request_is_killed_and_reported(self):
        found = self.launch(ignore_term=1)
        self.assertEqual(found["stopped"], "killed")
        self.assertIn("the app did not end on SIGTERM: killed", found["problems"])

    def test_helper_left_behind_is_reported_and_cleaned_up(self):
        self.build(self.helper)
        found = self.launch(spawn_helper=1)
        self.assertIn("processes from the bundle were still running afterwards", found["problems"])
        self.assertEqual(found["leftover_processes"], [f"{self.helper} child"])

    def test_new_crash_report_fails_the_launch_and_is_kept(self):
        # The stand-in cannot make the system write a report, so one appears while it runs.
        self.build(self.app / "Contents/MacOS/OmniWM")
        report = self.crashes / "OmniWM-2026-11-27.ips"
        original = SMOKE.start

        def start_and_crash(*arguments):
            started = original(*arguments)
            report.write_text("crash")
            return started

        with unittest.mock.patch.object(SMOKE, "start", side_effect=start_and_crash):
            found = SMOKE.launch(self.app, "direct", self.output, seconds=1, grace=2,
                                 crash_directories=(self.crashes,), report_delay=0)
        self.assertEqual(found["crash_reports"], [report.name])
        self.assertIn("the system wrote a crash report", found["problems"])
        self.assertEqual((self.output / report.name).read_text(), "crash")

    def test_command_line_checks_help_and_writes_the_report(self):
        self.build(self.app / "Contents/MacOS/OmniWM")
        for status, expected in ((0, 0), (2, 1)):
            self.build(self.app / "Contents/MacOS/omniwmctl", help_status=status)
            with contextlib.redirect_stdout(io.StringIO()):
                result = SMOKE.main([str(self.app), "--output-dir", str(self.output), "--seconds", "1",
                                     "--mode", "direct"])
            self.assertEqual(result, expected)
        report = json.loads((self.output / "smoke.json").read_text())
        self.assertIn("omniwmctl --help exited with 2", report["problems"])


if __name__ == "__main__":
    unittest.main()
