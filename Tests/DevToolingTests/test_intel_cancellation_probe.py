import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("intel_cancellation_probe",
                                              ROOT / "Scripts/intel/cancellation-probe/probe.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

IMPORTS = """/builds/weak-l/OmniWMTests [x86_64]:
    -imports:
      0x0000  _swift_task_isCancelled (from libswift_Concurrency)
      0x0001  _swift_task_isCancelledWithFlags [weak-import] (from libswift_Concurrency)
      0x0002  ____chkstk_darwin [weak-import] (from libSystem)
      0x0003  _swift_retain  (from libswiftCore)
"""
DISASSEMBLY = """/builds/OmniWMTests:
(__TEXT,__text) section
_$sGuarded:
0000000000000001\tcallq\t0x1 ## symbol stub for: _$ss26_stdlib_isOSVersionAtLeastyBi1_Bw_BwBwtF
0000000000000002\tcallq\t0x2 ## symbol stub for: _swift_task_isCancelledWithFlags
_$sUnguarded:
0000000000000003\tcallq\t0x2 ## symbol stub for: _swift_task_isCancelledWithFlags
_$sUnrelated:
0000000000000004\tretq
"""
INTERFACE = """extension _Concurrency.Task {
  @_alwaysEmitIntoClient @_transparent public var isCancelled: Swift.Bool {
    get {
      if #available(anyAppleOS 27, *) { return true }
      return false
    }
  }
}
@available(macOS 26.0, *)
@_silgen_name("swift_task_isCancelledWithFlags")
@usableFromInline
internal func _taskIsCancelledWithFlags(_ task: Builtin.NativeObject, flags: Swift.UInt64) -> Swift.Bool
public func unrelated()
"""


class IntelCancellationProbeTests(unittest.TestCase):
    def test_import_attributes_are_read_per_library_and_the_header_is_ignored(self):
        found = PROBE.imports(IMPORTS)
        self.assertEqual(found, {
            ("libswift_Concurrency", "_swift_task_isCancelled"): False,
            ("libswift_Concurrency", "_swift_task_isCancelledWithFlags"): True,
            ("libSystem", "____chkstk_darwin"): True,
            ("libswiftCore", "_swift_retain"): False,
        })

    def test_comparison_names_every_changed_import(self):
        baseline = {("a", "kept"): False, ("a", "weakened"): False, ("b", "strengthened"): True, ("b", "gone"): False}
        variant = {("a", "kept"): False, ("a", "weakened"): True, ("b", "strengthened"): False, ("b", "new"): True}
        self.assertEqual(PROBE.compare(baseline, variant), {
            "newly_weak": ["a weakened"], "no_longer_weak": ["b strengthened"],
            "only_in_baseline": ["b gone"], "only_in_variant": ["b new"],
        })

    def test_callers_report_whether_the_os_version_is_tested_first(self):
        self.assertEqual(PROBE.callers(DISASSEMBLY), {"_$sGuarded": True, "_$sUnguarded": False})

    def test_declarations_keep_their_attributes_and_a_transparent_body(self):
        getter, function = PROBE.declarations(INTERFACE)
        self.assertIn("#available(anyAppleOS 27, *)", getter)
        self.assertEqual(getter.splitlines()[-1], "  }")
        self.assertTrue(function.startswith("@available(macOS 26.0, *)"))
        self.assertNotIn("unrelated", function)

    def test_load_failure_and_partial_runs_do_not_pass(self):
        names = PROBE.test_names()
        self.assertEqual(len(names), 4)
        log = "".join(f"Test Case '-[{name.replace('/', ' ')}]' passed (0.001 seconds).\n" for name in names)
        self.assertTrue(PROBE.outcome("weak-l", 0, log, names)["all_passed"])
        self.assertFalse(PROBE.outcome("weak-l", 0, log.splitlines()[0], names)["all_passed"])
        failed = PROBE.outcome("baseline", 83, "Symbol not found: _swift_task_isCancelledWithFlags\n", names)
        self.assertEqual((failed["loaded"], failed["all_passed"]), (False, False))
        self.assertEqual(failed["missing_symbol"], ["_swift_task_isCancelledWithFlags"])


if __name__ == "__main__":
    unittest.main()
