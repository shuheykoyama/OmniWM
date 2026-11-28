import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("intel_glass_probe", ROOT / "Scripts/intel/glass_probe.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class IntelGlassProbeTests(unittest.TestCase):
    def test_program_takes_the_requirements_from_the_app_source(self):
        code = PROBE.program(PROBE.SOURCE.read_text())
        for name in ("enum EffectKey", "enum Effect:", "enum BodyProperty", "enum ElementProperty",
                     "enum BackdropProperty", "case diffuseHeightScale"):
            self.assertIn(name, code)
        self.assertNotIn("static let sdfClassNames", code)
        with self.assertRaisesRegex(RuntimeError, "not found"):
            PROBE.program("struct Other {}\n")

    @unittest.skipUnless(sys.platform == "darwin", "Reads the Core Animation of this machine")
    def test_probe_reports_every_checked_property(self):
        found = PROBE.probe()
        self.assertGreater(found["checked"], 20)
        self.assertEqual(sorted(found), ["architecture", "build", "checked", "macos", "missing"])


if __name__ == "__main__":
    unittest.main()
